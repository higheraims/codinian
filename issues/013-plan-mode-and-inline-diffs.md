---
id: ISSUE-013
title: Plan mode and inline Edit/Write diffs
status: done
type: feature
area: gui
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-005, ISSUE-012]
---

## Summary

Two rendering cases the generic tool card handles badly.

`ExitPlanMode` carries a plan the user is meant to read and accept or reject. Rendered as a
JSON blob in a tool card it is unreadable; it should render as the document it is, with
accept and reject as the approval buttons.

`Edit` and `Write` carry file content. Approving an edit means reading a diff, so render
`old_string` against `new_string` as a real diff with the file path as the heading. Judging
a change from a wall of quoted strings is how bad edits get approved.

## Acceptance / done-when

- A plan renders as formatted text with accept and reject controls.
- `Edit` renders as a diff of old against new, with the file path shown.
- `Write` shows the file path and the content it will write, marked as a new or replaced
  file.
- Long content collapses rather than pushing the rest of the transcript off screen.

## Notes & worklog

- Both live in the shared renderer from [[ISSUE-005]], so the browser and the embedded pane
  get them together.
- 2026-08-19: Tool input shapes checked against 52 stored transcripts rather than assumed.
  `Edit` carries `file_path`, `old_string`, `new_string`, `replace_all` (1328 calls);
  `Write` carries `file_path`, `content` (498); `ExitPlanMode` carries `plan` and
  `planFilePath`, once also `allowedPrompts` (5). **`MultiEdit` does not appear once** in
  those transcripts, so its handling is written to a shape that was specified rather than
  observed. It dispatches on an `edits` array being present, so it costs nothing when the
  tool never appears, but treat it as unconfirmed.
- 2026-08-19: The renderings apply in the approval card as well as the ordinary tool card,
  which is the point: approving an edit means reading a diff, and a wall of quoted JSON is
  how bad edits get approved. An `ExitPlanMode` approval reads "Plan ready for review" with
  Accept plan and Reject plan, and resolves to Accepted or Rejected instead of Approved or
  Denied.
- 2026-08-19: Collapsing reuses the existing "Show result" toggle rather than introducing a
  second mechanism. Content over 30 lines starts collapsed.
- 2026-08-19: The diff is a line-level LCS written for this, with no dependency, and falls
  back to a plain remove-all then add-all past 250k table cells so a large edit cannot hang
  the pane. Markdown for the plan covers headings, bullet and numbered lists with wrapped
  continuations, fenced code, inline code and bold, built through `h()` and `textContent`
  so plan text is never treated as markup.

## Resolution

Done. Verified in headless Chromium against a mock session built to exercise all three:
the Edit diff shows removals and additions against the file path, MultiEdit renders one
hunk per edit, Write collapses behind a toggle, and the plan renders as a formatted
document inside the approval card with Accept and Reject. Legible in both themes.
