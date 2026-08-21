---
id: ISSUE-036
title: An SDK session dies silently when its turn loop raises
status: open
type: bug
area: sdk
created: 2026-08-21
updated: 2026-08-21
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
