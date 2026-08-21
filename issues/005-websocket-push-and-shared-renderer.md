---
id: ISSUE-005
title: WebSocket event push and the shared transcript renderer
status: done
type: feature
area: remote
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-003, ISSUE-004, ISSUE-006]
---

## Summary

The remote server (remote/server.py, 62 lines) serves four REST endpoints and the browser
polls `/api/sessions` every 5 seconds and `/api/sessions/{id}/output` every 2. Polling an
HTML snapshot of a VTE buffer cannot express a tool card or an approval button, and an
approval that arrives 2 seconds late is an approval the user is already waiting on.

Add a WebSocket endpoint carrying the `TranscriptEvent` stream, plus a message for
resolving an approval. Then write the transcript renderer once, in HTML and JS under
`remote/static`, and use it in both places: the browser client and the WebKitGTK pane
embedded in the desktop window ([[ISSUE-006]]). One renderer, two hosts. That is the
decision that keeps the desktop and remote views from drifting apart.

First version of the renderer: text bubbles, collapsible thinking blocks, tool cards
(name, input, result), approval buttons, and a cost and token footer.

## Acceptance / done-when

- A client connects over WebSocket, receives the session's existing events, then live ones.
- Approve/deny sent over the socket resolves the pending future in the driver.
- The renderer draws each event type distinctly; an unknown event type degrades to
  something readable rather than breaking the pane.
- The existing REST session-list endpoints keep working (the sidebar and any scripts
  against them do not break).

## Notes & worklog

- Auth lands in [[ISSUE-007]] and is load-bearing here: approving a tool over this socket
  is remote code execution by design.
- 2026-08-19: The renderer pairs correctly against the real backend, which was the open
  question from the mock work. A captured live stream carries one `tool_use` and one
  `tool_result` sharing a `tool_use_id`, and the page draws one tool card that moves from
  pending to done. The mock re-emits `tool_use` after approval; the real stream does not.
- 2026-08-19: Approving from the pane did nothing. `resolveWith` in `app.js` sent no
  `session_id`, so the server looked up session `None` and answered
  `stale_or_unknown_request`, which `app.js` did not handle, so the card stayed dimmed and
  the turn stayed parked. Both halves are fixed: `resolve` carries `session_id`, and a
  server `error` reply is routed to the card by `request_id`, which re-enables the buttons
  and shows the reason. The mock hid this because it matches approvals on `request_id`
  alone, and the earlier WebSocket test hand-wrote a payload that already had `session_id`,
  so both exercised the protocol rather than the page.

## Resolution

Done. Verified by clicking Approve in headless Chromium against the running app: the page
sends `session_id`, the backend emits `approval_resolved`, `tool_result` and `usage`, and
the card collapses to a one-line outcome. Auth for this socket remains [[ISSUE-007]].
