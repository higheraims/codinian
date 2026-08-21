---
id: ISSUE-038
title: The web composer silently no-ops for terminal sessions
status: open
type: bug
area: remote
created: 2026-08-21
updated: 2026-08-21
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
