---
id: ISSUE-037
title: Quitting never disconnects live SDK sessions
status: open
type: bug
area: sdk
created: 2026-08-21
updated: 2026-08-21
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
