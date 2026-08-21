---
id: ISSUE-012
title: Permission-mode switching mid-session
status: done
type: feature
area: gui
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-004, ISSUE-014]
---

## Summary

`permissionMode` decides which tools auto-approve and which reach `canUseTool`: `default`,
`acceptEdits`, `bypassPermissions`, `plan`. Set once at session start ([[ISSUE-004]]) it is
a blunt instrument. The usual pattern is to review carefully for the first few edits, then
switch to `acceptEdits` once the work looks right, then back when the session moves into
unfamiliar code.

Expose the mode as a control on a running session, showing which mode is active at a glance
so `bypassPermissions` is never a state you are in by accident.

## Acceptance / done-when

- The active mode is visible on any running `sdk` session.
- Changing it takes effect on the next tool call without restarting the session.
- A mode change is recorded in the transcript as a `system` event, so the log explains why
  approvals stopped appearing.
- Switching to `bypassPermissions` requires a deliberate confirmation.

## Notes & worklog

- Per-folder defaults are [[ISSUE-014]]; this issue is the per-session override.
- 2026-08-19: `ClaudeSDKClient.set_permission_mode` exists and takes `default`,
  `acceptEdits`, `plan`, `bypassPermissions`, `dontAsk` or `auto`. `SdkSession` wraps it,
  updates the session, and emits a `system` event with subtype `permission_mode` so every
  client sees the change and the transcript records when it happened.
- 2026-08-19: The server validates the mode against that set and answers
  `unknown_permission_mode` rather than passing a typo down to fail mid-turn, and
  `unknown_session` for an id it does not have.
  Verified live: switching to `acceptEdits` took effect in session meta and emitted the
  system event; `nonsense` was rejected. Remaining: the control in the UI.
- The SDK applies a mode change to the next tool call, so switching mid-turn affects the
  following tool onward rather than the one already in flight.- 2026-08-19: UI done. The pane header carries a mode selector for `sdk` sessions listing
  all six modes. Choosing `bypassPermissions` asks for confirmation first, since it turns
  off the gate this whole application is built around, and cancelling puts the selector
  back. A rejection from the server reverts the selector and says why. The change also
  arrives as a `system` event, rendered as "Permission mode changed to X. Takes effect on
  the next tool call.", so the transcript records when it happened and a second client
  stays in step.

## Resolution

Done. Verified live: switching a running session to acceptEdits took effect against the
real SdkSession and appeared in session meta and in the transcript; an invalid mode was
rejected with `unknown_permission_mode`.
