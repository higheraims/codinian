---
id: ISSUE-046
title: Commit form takes a subject and a body, not one title field
status: done
type: feature
area: remote
created: 2026-08-27
updated: 2026-08-27
related: [ISSUE-020]
---

## Summary

The git tab's commit form was a single textarea labelled "Commit message", which
in practice is the subject line: `git log --oneline`, `git shortlog` and every
forge show only the first line of it. Anyone with more to say than fits there had
the choice of writing a title far too long for those views, or leaving the
reasoning out. Git's own model is two parts, a subject and a body separated by a
blank line, and the form should offer both.

## Acceptance / done-when

- The form has a one-line summary field and a separate optional description.
- Both reach git as they would from `git commit`: subject, blank line, body.
- A description on its own does not commit; the summary is still required.
- An empty or whitespace-only description produces a commit with no body, not one
  padded with blank lines.
- A request that sends only `message`, as an older client would, still commits.

## Notes & worklog

- `vcs.commit` takes `body` and passes it as a second `-m`. Git inserts the blank
  line between the two, so no separator is assembled here.
- `-m` implies `--cleanup=whitespace` rather than `strip`, so a body line starting
  with `#` stays in the message instead of being read as an editor comment. Worth
  knowing before anyone switches this to a temp file and `-F`.
- The summary field carries a character count that appears past 50 and turns amber
  past 72: 50 is roughly what a forge's commit list shows before truncating, 72 is
  where git's documentation stops. Neither is enforced. A `maxlength` that eats
  keystrokes mid-word is worse than a number that goes amber.
- The count is positioned inside the summary field so both inputs keep the same
  width.

## Resolution

`vcs.py` (`commit`), `remote/projects_api.py` (`git_commit`),
`remote/static/project.js` (form and `doCommit`), `remote/static/project.css`,
and the endpoint's entry in `docs/project-workspace-protocol.md`.

Checked by driving the page: typing into both fields, checking paths, clicking
Commit, then reading the message back out of `git log` — subject and body landed
in the right places and a `#123` line survived. Also checked a commit with no
description, one with a whitespace-only description, and a description with the
summary left empty.
