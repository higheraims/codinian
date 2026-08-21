---
id: ISSUE-004
title: SDK session driver with PreToolUse-hook approvals
status: done
type: feature
area: sdk
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-002, ISSUE-003, ISSUE-008]
---

## Summary

Build `sdk_session.py`: one `ClaudeSDKClient` per session, interactive and multi-turn.
It maps SDK messages to the `TranscriptEvent`s from [[ISSUE-003]] and pushes them to the
manager. Sending a message is `client.query(text)`, replacing the "type into the terminal"
injection path for `sdk` sessions.

`canUseTool` is the piece that earns the pivot. When it fires, the driver creates an
`approval_request` event plus a pending future keyed by `tool_use_id`, broadcasts the
event, and awaits resolution from any attached client. The callback blocks the session
until it returns, which is the whole mechanism and also the hazard: with no client
attached, or after an app restart, the session sits there. That behavior gets documented
rather than hidden, and [[ISSUE-008]] handles the concurrency and staleness cases.

Concurrency shape: one asyncio loop runs the SDK sessions and the aiohttp server in a
dedicated thread; GTK stays on the main thread. GTK to async goes through
`loop.call_soon_threadsafe`, async to the GTK sidebar through `GLib.idle_add`.

## Acceptance / done-when

- An `sdk` session starts in a chosen working directory, accepts a message, and streams
  mapped events to the manager.
- A tool call raises an `approval_request` event; approving lets the tool run, denying
  reports the denial back to the model.
- Two sessions run at once without blocking each other: one waiting on an approval does
  not stall the other.
- What happens to an in-flight approval when the app quits is written down in
  `docs/remote-access.md` or the README, not left to be discovered.

## Notes & worklog

- `permissionMode` (`default`, `acceptEdits`, `bypassPermissions`, `plan`) decides which
  tools auto-approve and which reach the approval path. The per-session setting is
  [[ISSUE-012]]; the driver just needs to accept the mode at start.
- 2026-08-19: Approval path switched from `can_use_tool` to a **PreToolUse hook**. The
  M0/M1 spikes ([[ISSUE-002]]) showed `can_use_tool` is shadowed by whole-tool
  `allowed_tools` entries and by the user's `~/.claude` allow rules, so it would silently
  auto-approve. A PreToolUse hook fires for every tool even when those rules exist, so we
  keep the project's `CLAUDE.md` / skills / settings context loaded (`setting_sources`
  stays default) and still gate each call. The hook is async, so it suspends on a future
  until any client answers; `permissionDecision` is `allow`/`deny`, and `updatedInput`
  carries edit-and-approve. Proven blocking both ways in `spike3_hook.py`.
- 2026-08-19: Built `sdk_session.py` (`SdkSession` + `SdkRuntime`) and verified the whole
  backend path headlessly (`test_ws_integration.py`): a real sdk session driven over the
  WebSocket streamed all nine event types in order (no seq gaps), the approval round-trip
  blocked then allowed a `Write`, the file was created, and the `usage` event reported
  cost. GTK/WebKitGTK shell is wired (`window.py`) but not yet run live.
- Pending-approval-on-quit hazard is now written up in `docs/remote-access.md`.
- 2026-08-19: Live-tested in the running app. Three startup faults found and fixed. A new
  session emitted `initializing` and nothing else until the first prompt, because the SDK
  stays silent until the first query (its init message rides on that response); `start()`
  now emits `awaiting_input` once `connect()` returns. A failed `connect()` was a swallowed
  task exception that left the session looking hung; it now emits an error event and status
  and the half-built session is dropped. `create_threadsafe` dereferenced the asyncio loop
  before the server thread bound it, so it now waits on an event with a 10s budget.
- 2026-08-19: A failed `create` over the WebSocket used to propagate out of the message
  handler and close the client's socket. The server answers `session_start_failed` with the
  reason and keeps serving; any other handler exception answers `request_failed`.

## Resolution

Done. `sdk_session.py` drives sessions through the PreToolUse hook, and the approval
round-trip was verified against the real SDK from both a script and the desktop app:
`tool_use` then `approval_request`, blocked until answered, then `approval_resolved`,
`tool_result` and `usage`. Startup and failure paths report status instead of stalling.
