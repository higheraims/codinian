---
id: ISSUE-037
title: Quitting never disconnects live SDK sessions
status: done
type: bug
area: sdk
created: 2026-08-21
updated: 2026-08-24
related: [ISSUE-036]
---

## Summary

`SdkSession.close()` exists to `await self._client.disconnect()` and tear down a
session's `claude` subprocess cleanly, and `SdkRuntime.close_all()` exists to do
that for every live session. Neither has a caller. `window.py`'s close handler
only saves window geometry; `main.py` installs no shutdown handler. The server
loop runs in a daemon thread, so when the GTK main loop exits the process ends
and that thread -- with whatever subprocesses it owns -- is cut off rather than
told to disconnect.

## Acceptance / done-when

- Closing the app calls `SdkRuntime.close_all()` (or equivalent) so each live
  session's `claude` subprocess is disconnected before the process exits.
- Verified: start a session mid-turn, quit, confirm no orphaned `claude`
  process is left behind.

## Notes & worklog

- 2026-08-21: Found in the pre-public review. `close_all` is defined at
  `sdk_session.py` and referenced nowhere (`grep -rn close_all`). Needs a live
  run to confirm the subprocess actually lingers versus dying on a closed pipe,
  which is why it was filed rather than fixed blind.

## Resolution

`CodinianApp` connects `Gio.Application::shutdown` to `_on_shutdown`, which calls
a new `SdkRuntime.close_all_threadsafe()`.

The threadsafe wrapper waits, where `close_threadsafe` does not, and the
docstring says why: the asyncio loop lives in a daemon thread, so the moment the
GTK main loop returns the process ends and that thread goes with it. Waiting is
the entire point. It is bounded at `SHUTDOWN_TIMEOUT = 5.0` seconds, because a
subprocess that will not go is not worth hanging a quit on, and it swallows
exceptions for the same reason.

The window's `close-request` handler was the other candidate and is the wrong
one. It runs per window; `shutdown` runs once, on the way out, whatever closed
the app.

Verified in three steps, because the issue asked for a live run and each link
needed its own check.

`close_all_threadsafe` itself: a real `SdkRuntime` on a loop in a thread, one
real session started and answered, `pgrep -P` showing the bundled `claude` child.
The call returned in 0.18 s and the child was gone.

The GTK half: a bare `Adw.Application` whose window closes on a timer prints its
shutdown handler running before `run()` returns, so the signal does fire on an
ordinary window close and does so while the process is still alive.

Then both together, running the real `CodinianApp` under a second config: one
live SDK session with child 122472, `win.close()`, and no `claude` child left
either inside the shutdown handler or after `run()` returned.

One thing the issue guessed at turns out not to hold, and is worth recording so
nobody re-files it. The subprocess does not linger when the parent dies without
being told: sending SIGTERM to a running instance with a live session took its
`claude` child with it, through the closed pipe. So this was never leaking
processes. What it was doing was tearing the pipe out rather than disconnecting,
which is what `close()` exists to avoid.
