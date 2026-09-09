---
id: ISSUE-056
title: A message typed mid-turn waits for the turn to end
status: done
type: bug
area: sdk
created: 2026-09-09
updated: 2026-09-09
related: [ISSUE-028, ISSUE-033, ISSUE-054]
---

## Summary

Asked whether there is a way to redirect a session that looks stuck without
hitting Stop: an agent waiting on a shell command that may have hung, where the
thing wanted is a word with the agent rather than the end of its turn.

There is, and Codinian was the only thing preventing it. The CLI keeps a command
queue. A user message written to it while a turn is running is absorbed by that
turn at the next step of the agent loop, so the model reads it without the turn
having to end. `SdkSession._run` held each message until the `ResultMessage`
arrived, so nothing ever reached the CLI mid-turn and the feature was invisible.

The reasoning for holding it is in the `_run` docstring from [[ISSUE-054]]: a
prompt typed while Claude is working belongs to the next turn, not the middle of
this one. The ordering it protects is real, but the CLI provides it, and
provides it better, because its queue can reach into a running turn.

Left open by [[ISSUE-028]]: "Not addressed: whether the CLI interrupts the
running turn to act on the message or finishes first." This answers that.

## Acceptance / done-when

- A message sent while a turn is running reaches the model during that turn.
- Messages still arrive in the order they were typed, one writer at a time.
- Stop cancels the queued messages as well as the turn, so a message typed a
  second before Stop does not start a fresh turn after it.
- A cancelled message is named in the transcript rather than left looking sent.

## Notes & worklog

Measured against the CLI at `/usr/bin/claude` through `claude-agent-sdk`
0.2.140, driving `ClaudeSDKClient` directly.

Absorption, against a turn told to run four separate foreground shell commands
of 15 seconds each:

    [  8.8s] tool_use: Bash  python3 -c 'import time; time.sleep(15)'  (1 of 4)
    [ 20.6s] second query(): "skip the remaining sleeps, reply STEERED"
    [ 24.4s] tool_result: (Bash completed with no output)
    [ 29.1s] text: STEERED

Calls 2, 3 and 4 never ran. Held behind the turn, the same message would have
arrived after all four, 40 seconds later.

The limit, from the same rig with one 240-second command instead of four short
ones: the message went out at 25 seconds and nothing happened for the next 90.
Absorption is between tool calls, not during one. The CLI does document a Bash
outcome `interrupted`, described in its own prompt text as a queued user message
stopping a shell command mid-execution, but that did not reproduce over this
path and nothing here relies on it. A command that has genuinely hung is still
Stop's problem, which is why the two are being kept as separate controls rather
than folded together.

`interrupt` takes a `cancel_queued` field the Python SDK does not expose:
`ClaudeSDKClient.interrupt()` takes no arguments and `SDKControlInterruptRequest`
is `{subtype}` alone. Sent as a raw control request with a message already
queued, the CLI answers:

    {"still_queued": [], "cancelled": ["e371a22f-debd-4841-88c5-068bad7eb3a1"]}

which is the uuid we stamped on the inbound message. Without a uuid the CLI
emits no lifecycle for a message and has nothing to list, so the stamp is what
makes the report possible.

Where the capability list lives is not where it looks. `get_server_info()`
returns `commands`, `models`, `agents` and eleven other keys, and no
`capabilities` at all; the list arrives on the init message, which the CLI sends
on the first turn:

    ["interrupt_receipt_v1", "interrupt_cancel_queued_v1", "msg_lifecycle_v1"]

## Resolution

`_run` no longer waits for the turn to end before handing over the next message.
It stays a single consumer, which is what actually kept sends ordered and
serialised; the `_turn_done` event it waited on had no other reader and is gone.

Each message now carries a uuid of our own, which means building the stdin
envelope rather than passing a string to `query`, whose string form stamps none.
`_sent_text` keeps the last 64 messages against their uuids so a cancellation
can quote what it threw away.

`interrupt` sends `cancel_queued: true` through the client's control channel and
reports the `cancelled` uuids as a system line. Two things gate that: the CLI
advertising `interrupt_cancel_queued_v1`, and `_send_control_request` still
existing on the SDK's query object, which is private. Either one missing falls
back to `ClaudeSDKClient.interrupt()`. Capabilities are unknown until init
arrives, and unknown is treated as capable, because the field is documented as
ignored by CLIs that predate it and the alternative disables this during the
first turn.

One thing had to move that is not about steering. `_run` set the status to
working for every message it handed over, which was harmless while messages
only ever started a turn. A message sent mid-turn now reaches that line while a
turn is running, and a message typed while an approval card is on screen would
have replaced `awaiting_approval` with `working` and hidden a card still waiting
for an answer. The status is now set only when nothing is in flight.

In `app.js` the mid-turn placeholder said "queued until this turn ends", which
was accurate when it was written and is now the opposite of what happens. It
reads "Message... (goes to Claude mid-turn)". Stop is unchanged and still shown
only while there is something to stop, per [[ISSUE-033]].

### What was checked

Driving a real `SdkSession` against a real CLI, reading the events the manager
emits rather than the SDK's messages.

Absorption. The status moves on the first prompt and not on the second, which
is the guard above doing its job:

    [  0.0s] status: initializing
    [  0.6s] text[user/operator]: Run four SEPARATE foreground Bash calls...
    [  0.6s] status: working
    [  6.6s] tool_use: Bash  python3 -c 'import time; time.sleep(15)'
    [ 20.7s] text[user/operator]: Change of plan: skip the remaining sleeps...
    [ 24.9s] text[assistant]: STEERED. Skipped sleeps 2-4; the first one completed.

Stop, with two messages sent into the same running turn a second apart:

    [ 77.5s] text[user/operator]: Change of plan: skip the remaining sleeps...
    [ 78.5s] text[user/operator]: and also say BANANA
    [ 79.0s] system[note]: Stopped before reading 2 queued message(s):
             "Change of plan: skip the remaining sleeps. Reply STEERED no…";
             "and also say BANANA"
    [ 79.0s] text[user/injected]: [Request interrupted by user for tool use]
    [ 79.0s] status: awaiting_input

And the blocked case, one 240-second command with a message sent 25 seconds in:
nothing for the next 40 seconds, then Stop cancelled it, which is the tool
boundary doing what it does.
