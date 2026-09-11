---
id: ISSUE-054
title: Output written after a turn's result waits for the next prompt
status: done
type: bug
area: sdk
created: 2026-08-30
updated: 2026-08-30
related: [ISSUE-036, ISSUE-053]
---

## Summary

Reported as chat that arrives out of order, and narrowed down over several
rounds. The detail that identified it:


> the session seems to be awaiting input, but as soon as I give a prompt, some
> other stuff appears below the prompt instantly, which seems like an instant
> response but also seems more relevant to the previous turn.

Below the prompt is the whole diagnosis. The missing output is not hidden and
not unpainted; it is appended after the prompt echo, because that is when it
arrives.

`_run` reads with `receive_response()`, which the SDK documents as terminating
immediately after the first `ResultMessage`. Nothing reads the CLI between that
point and the next `query()`. Meanwhile the SDK does keep reading: `Query`
spawns a detached `_read_messages` task at connect that parses CLI output
continuously into a memory stream, whatever the consumer is doing.

So anything the CLI writes after the result of turn N is read by the SDK, queued,
and handed over at the top of turn N+1, arriving after that turn's prompt echo
and looking like an instant answer to the wrong question. The turn is reported as
finished either way, because `_emit_status(AWAITING_INPUT)` fires when
`receive_response()` returns rather than when the stream goes quiet.

## Acceptance / done-when

- Anything the CLI emits is rendered when it arrives, including between turns.
- A prompt typed while a turn is running still waits for that turn rather than
  being interleaved into it.
- A message that fails to render still costs only itself, and a dead pipe still
  stops the session and says so (ISSUE-036).

## Notes & worklog

Three earlier readings were wrong, and each was ruled out by measurement rather
than argument. A dropped WebSocket with a replay on reconnect: no, there was no
reconnect. The composer covering the transcript: real, measured at 168px, fixed
in [[ISSUE-053]], symptom unchanged. A WebKitGTK repaint stall: ruled out by
"below the prompt", since stale pixels cannot reorder the DOM.

Confirmed in the installed SDK rather than assumed:

- `client.py:568` -- `receive_response` is `async for ... yield ...; if
  isinstance(message, ResultMessage): return`.
- `_internal/query.py:288` -- `self._read_task = spawn_detached(self._read_messages())`.
- `_internal/query.py:399` -- that task sends every parsed message into
  `_message_send`, the stream `receive_messages` reads from.

Why it is intermittent: it needs the CLI to write something after the result.
A `rate_limit_event` pushed as the turn closes will do it, as will anything else
that lands in that window.

## Resolution

The reader is now one task for the session's lifetime over `receive_messages()`,
instead of a per-turn `receive_response()`. There is no window where nobody is
reading, so there is nothing to hold over.

Sending stays serialised, which was the reason the two were one loop in the first
place. `_run` keeps its queue and now waits on a `_turn_done` event that the
reader sets when a `ResultMessage` goes past, so a prompt typed mid-turn still
waits for that turn instead of being interleaved into it.

The turn is declared over from the same place, which is the other half of the
bug: `AWAITING_INPUT` now follows the result through the reader rather than
following the iterator's return.

### What was checked

The real `SdkSession`, old copy and working copy, against a client that writes
an assistant block, the result, and then one more assistant block after it, over
one shared message stream like the SDK's own. Two prompts, and where the
trailing block lands:

    old loop                                 new loop
    > user: prompt one                       > user: prompt one
    > assistant: turn 1: the answer          > assistant: turn 1: the answer
    > user: prompt two                       > assistant: TRAILING
    > assistant: TRAILING                    > user: prompt two
    > assistant: turn 2: the answer          > assistant: turn 2: the answer

The old column is the bug report: content under the next prompt, instant,
belonging to the turn before.

The two things the split could have broken, both checked on the new loop. Two
prompts sent back to back during one slow turn produce
`query(one) -> result of turn 1 -> query(two) -> result of turn 2`, so a prompt
typed mid-turn still waits. A message stream that ends mid-session takes the
session to `error`, says "the connection to the claude process ended", and
refuses further sends, which is ISSUE-036's behaviour reached from the reader
instead of the turn loop.
