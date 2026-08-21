---
id: ISSUE-001
title: Put the project under version control
status: done
type: chore
area: packaging
created: 2026-08-16
updated: 2026-08-19
related: []
---

## Summary

`~/Projects/codinian` is not a git repository; `git status` reports "not a git
repository (or any parent up to mount point /)". Every other project in `~/Projects` that
carries an issue tracker keeps it in git, and this tracker's promise (an `ISSUE-NNN`
reference resolves forever, a `status:` change is attributable to a commit) leans on
versioned history. Without it there is also no way to see when a file changed, no way to
undo a bad edit across files, and no `git log` to date the work these issues describe.

## Acceptance / done-when

- `git init` in the project root, with a `.gitignore` covering `__pycache__/`, `*.pyc`,
  `.venv/`, and any local SQLite copies.
- One initial commit of the current tree, including `docs/issues/`.
- `~/.local/share/codinian/codinian.db` stays out of the repo; it is user data, not source.

## Notes & worklog

- 2026-08-16: Raised while setting up this tracker.
- 2026-08-19: `git init -b main`; first commit `4451b83`, 45 files tracked.

## Resolution

Repository initialized on branch `main` with commit `4451b83`. The `.gitignore`
covers `__pycache__/`, `*.py[cod]`, build artifacts, `.venv/`, in-tree
`*.sqlite`/`codinian.db` copies, the per-machine `.claude/settings.local.json`,
and the FrontMatter plugin cache. The live database at
`~/.local/share/codinian/codinian.db` is outside the repo and stays untracked as
user data. The large art sources under `docs/art/` (`34526.eps`, the stock-pack
`.zip`) are ignored and remain on disk; only `codinian.svg`, the asset the UI
renders, is tracked. No remote is configured yet; add one when the code is ready
to push.
