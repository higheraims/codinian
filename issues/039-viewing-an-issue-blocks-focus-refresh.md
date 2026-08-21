---
id: ISSUE-039
title: Viewing an issue disables the workspace's focus-refresh
status: open
type: bug
area: remote
created: 2026-08-21
updated: 2026-08-21
related: []
---

## Summary

`refreshFromDisk()` in `remote/static/project.js` refuses to run while
`isEditing()` is true, and `isEditing()` counts `state.issueDraft` as editing.
But `loadIssue()` sets `issueDraft` the moment an issue is opened for viewing --
there is no separate read-only mode -- and nothing clears it short of loading a
different issue. So once a user has looked at any single issue, focus-refresh
stops firing for the rest of the page's life, even after switching to the Files
or Git tab.

## Acceptance / done-when

- Merely viewing an issue does not permanently block focus-refresh.
- An issue with genuinely unsaved edits still blocks a refresh that would
  discard them.

## Notes & worklog

- 2026-08-21: Found in the pre-public review. Not fixed in that pass because the
  safe fix is dirty-tracking (block refresh only when the draft diverges from
  the loaded issue); the current behaviour is conservative -- it refreshes less
  often, it does not lose data -- so a half-done fix would be worse than filing.
