---
id: ISSUE-036
title: An SDK session dies silently when its turn loop raises
status: done
type: bug
area: sdk
created: 2026-08-21
updated: 2026-08-24
related: []
---

## Summary

`SdkSession._run` is a single asyncio task, created once in `start()` and never
recreated. If anything in the turn loop raises -- `_handle_message`,
`_handle_block`, `_capture_plan_usage`, or the SDK client itself losing its pipe
to the `claude` subprocess -- the outer `except Exception` emits one `error`
event, sets status `ERROR`, and the task returns. Nothing restarts it.

`send()` does not know the loop is gone. It still emits the user's text into the
transcript and pushes onto `self._sends`, which is now a queue with no consumer.
The message appears sent and is lost. The WebSocket `send` path
(`remote/server.py`) has no session-state guard either, so a browser or phone
client hits the same dead end with no feedback.

## Acceptance / done-when

- A raise inside the turn loop leaves the session in a state where `send()`
  either refuses with a visible error or the loop recovers; a message is never
  silently swallowed.
- The desktop composer and the web composer both reflect an errored session so
  the user knows to restart it.

## Notes & worklog

- 2026-08-21: Found in the pre-public review. Not fixed in that pass because the
  correct behaviour (recover the loop vs. surface a hard error and disable the
  composer) needs deciding and verifying against a live session, not a static
  read. `sdk_session.py` around `_run`/`send`.

## Resolution

The choice the worklog left open was recover the loop or surface a hard error.
The answer is both, split by what raised, because the two failures in the issue's
list are not the same kind of thing.

A raise out of `_handle_message` is a bug in our own mapping of one block. The
conversation is fine and the client is fine, so killing the session over an
unrenderable block is the wrong trade. Those are caught per message inside the
`receive_response` loop, reported as a `system` error event saying which message
could not be rendered, and the turn carries on.

A raise from the client is different. The pipe to the `claude` subprocess is
gone, there is nothing to carry on with, and the loop has to stop. That path now
runs `_fail`, which sets `_failed`, emits the error, drains anything still queued
behind the failure and says how many messages did not get delivered, then sets
the status to `error`.

`send` returns a bool and refuses when the session cannot take the message. It
checks twice, and the docstring says why: the echo that puts the user's text in
the transcript happens at queue time, so queueing onto a dead session would leave
their words sitting there looking sent. The state is read once for the caller's
answer and again on the loop thread, where `_failed` is actually written. A
refusal emits its own event: "This session has stopped and cannot take new
messages. Resume it from History to carry on."

`SdkRuntime.send` passes that through, and the WebSocket handler answers
`{"t": "error", "error": "session_not_running"}` rather than discarding it, which
is the browser and phone half of the same hole.

Both composers reflect it, and they are one composer: the desktop pane is a
WebKitGTK view of the same `remote/static` page, so `updateComposerState` covers
both. A session at status `error` disables the input and Send and reads "This
session stopped, resume it from History to carry on". Since a mapping failure no
longer sets that status, `error` now means the loop has stopped for good.

Verified with a stand-in client rather than by waiting for the real one to break.
Against a client whose `query` raises: the first send is accepted, the transcript
shows the error, the status goes to `error`, the second send is refused and says
so, and nothing is swallowed. Against a client that yields one message the mapper
chokes on: two consecutive turns both complete, the session stays at
`awaiting_input`, and each bad message leaves a note of its own. In the page, an
SDK session driven to `error` produced `inputDisabled: true, sendDisabled: true`,
and the live server answered `session_not_running` to a send naming an unknown
session.
