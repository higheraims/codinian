---
id: ISSUE-039
title: Viewing an issue disables the workspace's focus-refresh
status: done
type: bug
area: remote
created: 2026-08-21
updated: 2026-08-24
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

## Resolution

Dirty tracking, as the worklog expected.

`state.issuePristine` holds a signature of the draft as it was loaded, and
`issueDirty()` compares the live draft against it. `isEditing()` calls that
instead of testing whether `issueDraft` exists at all. The signature is
`issueDraftSignature`: id, title, status, type, area, related and every section's
heading and body, JSON-encoded in a fixed order, which is exactly the set
`saveIssue` sends. `extraFrontmatter` is left out because it is carried through
verbatim and no control in the editor can change it.

Three places take the snapshot: `loadIssue` after cloning, `saveIssue` after a
successful write, and `startNewIssue`. That last one matters more than it looks.
A blank new-issue form is not an edit, and refusing to refresh because one is
open would have reintroduced the bug in a smaller shape.

The draft is written on every keystroke already (`titleInput`, the section
heading and body textareas, the related-issue list all update it on `input` or
`change`), so the comparison sees a change as soon as one is made rather than at
some later commit point.

Verified against the real project workspace, driving `project.html` in headless
Chromium with the codinian project open on its Issues tab and calling
`window.codinianRefresh()` at each step: nothing open, true; after opening
ISSUE-039 and reading it, true; after typing into a section body, false. The
middle one is the bug, and it returned false before this change.
