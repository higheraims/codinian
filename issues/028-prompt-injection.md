---
id: ISSUE-028
title: Prompt injection not working
status: done
type: bug
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-025, ISSUE-026]
---

## Summary

Prompt injection doesn't seem to work - wanted to insert a /btw but the input text area doesn't accept input - both on local and over remote. Text in the input area just says "waiting..."

## Acceptance / done-when

- The composer takes input while a turn is running, on the pane and in the browser.
- A message sent mid-turn is visible in the transcript rather than disappearing until the turn ends.

## Notes & worklog

- Reproduced on both clients, which was the clue: they share `app.js`.

## Resolution

Frontend only, and only one condition. `updateComposerState` disabled the input
and the Send button whenever the session status was `working` or
`awaiting_approval`, and set the placeholder to "waiting...". Both clients load
the same `app.js`, which is why the pane and the browser behaved identically.

Nothing underneath needed the box closed. `SdkSession._sends` is an
`asyncio.Queue` drained by `_run` between turns, and the CLI queues on its own
side as well: the JSONL for the issue-24 session carries `queue-operation`
records, `enqueue` then `dequeue`, for the prompt that started it. So a message
sent mid-turn was always going to land. The composer was the only thing
stopping it.

It now closes only when no session is selected. While a turn is running the
placeholder reads "Message... (queued until this turn ends)", which says what
will happen rather than refusing. Verified by driving the real page against the
real server with the session parked in `awaiting_approval`, wrapping
`WebSocket.prototype.send` and clicking the button: the frame goes out as
`{"t":"send","session_id":"test0001","text":"/btw prefer the smaller diff"}`.

The message also shows up now, which it did not before for any prompt at all.
`SdkSession.send` emits it as a `text` event with `source: "operator"` at the
moment it is queued, so a message that waits several minutes for the current
turn is visible for those minutes instead of being swallowed. See [[ISSUE-026]],
which needed the same echo for a different reason.

Not addressed: whether the CLI interrupts the running turn to act on the
message or finishes first. The `queue-operation` records say it queues, and
nothing here changes that. A message that must land immediately still needs the
interrupt path.
