---
id: ISSUE-038
title: The web composer silently no-ops for terminal sessions
status: done
type: bug
area: remote
created: 2026-08-21
updated: 2026-08-24
related: []
---

## Summary

`updateComposerState()` in `remote/static/app.js` gates only on `!!meta`, while
every other footer feature gates on `meta.kind === 'sdk'`. A running
`terminal`-kind session therefore shows an active composer and a Stop button. But
`SdkRuntime.send`/`interrupt` look the id up only among SDK sessions and return
`False` for a terminal one, and the WebSocket handler ignores that return value.
Typing a message and pressing Send, or clicking Stop, does nothing, with no
feedback. The server has a real injection path for terminal sessions
(`inject_session` / `manager.queue_inject`), but the browser composer never
calls it.

## Acceptance / done-when

- A terminal session in the web UI either hides the composer/Stop, or wires them
  to the inject path so they actually work.

## Notes & worklog

- 2026-08-21: Found in the pre-public review. Lower urgency because terminal
  sessions are the pre-SDK path, but a silent no-op is the wrong failure mode.
  Cheapest fix is to gate the composer on `kind === 'sdk'` like its neighbours.

## Resolution

Gated on the kind, which is the cheaper of the two options the issue offered and
the honest one. Wiring the composer to `inject_session` would have let a user
type into a session whose output the browser never shows: terminal sessions are
mirrored as HTML through `/api/sessions/{id}/output`, which the web client does
not fetch, so the reply to anything typed would have gone nowhere visible.

`updateComposerState` now computes `writable = meta.kind === 'sdk'`, and the
input, the Send button and the Stop button all follow it, joining the permission
mode control and the totals row which already gated the same way. The placeholder
says which way to go: "This is a terminal session, drive it from the desktop app".

The server stopped ignoring the return value too. `SdkRuntime.send` was already
returning False for a session it does not own, and the WebSocket handler now
answers `{"t": "error", "error": "session_not_running"}` instead of dropping it,
with wording for that code in the client's `SERVER_ERROR_TEXT`. `interrupt` still
says nothing on failure, for the reason already in the comment there: a stop
click that lands after the turn ended is not a failure worth reporting.

Verified in the page rather than by reading it. The demo data had no terminal
session, which is why this shipped broken and is why `mock.js` now carries one
(`SESSION_E`, "Terminal: log triage", status `working`, the state that used to
show an active composer and a Stop button). Loading `/?mock=1` in headless
Chromium and clicking that row: `inputDisabled: true, sendDisabled: true,
stopHidden: true`, placeholder as above. Clicking an SDK session in the same
page leaves the composer enabled, so the gate is not over-wide.
