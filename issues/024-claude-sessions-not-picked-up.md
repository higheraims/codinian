---
id: ISSUE-024
title: Claude sessions not picked up
status: done
type: bug
area: db
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-009, ISSUE-020]
---

## Summary

Sessions already run in claude for a particular repo do not show up in sessions tab

## Acceptance / done-when

- Find a way to seamlessly integrate this so previous Claude sessions which are run against a repo will show up in the sessions tab

## Notes & worklog

- Either we are tracking sessions differently or we aren't picking up claude sessions correctly

## Resolution

Both guesses in the worklog were wrong in the same direction: nothing was being
picked up incorrectly, because nothing was being picked up at all. Codinian
keeps live sessions in memory, the SQLite file holds metadata it never reads
back, and the tab listed only what was in memory. Close the app and the tab is
empty again. The durable record has always been the CLI's own JSONL under
`~/.claude/projects`, which `claude_history.py` has been reading since
ISSUE-009, but only for the desktop Resume dialog.

So `list_prior_sessions` grew an `under` filter and the tab reuses it.
`GET /api/projects/{id}` now carries a `history` list beside `sessions`: every
conversation whose recorded `cwd` is inside the project, newest first, with the
first line of its first message as a title and the transcript's mtime. The
filter compares resolved path components, so `/x/proj` never claims a session
run in `/x/proj-other`, and a session run in a subdirectory of the project does
count as being in it.

Each earlier conversation carries a Resume button, which POSTs to a new route,
`POST /api/projects/{id}/sessions`. That starts an SDK session with the stored
transcript replayed into it, the same thing the Resume dialog does, and the
session then behaves like any other. Three limits on it:

- The id has to be one this project owns, or the answer is 404. A project route
  is not a way to reopen some other repo's conversation over the tailnet.
- Resuming something already open hands back the running session with
  `"existing": true` rather than starting a second session appending to one
  transcript.
- The working directory is the one the prior session recorded, not the project
  root, since the two are often not the same.

An entry that already has a live session drops out of the earlier list rather
than appearing twice, and the permission mode comes from the repo's
`default_permission_mode` unless the caller names one.

Checked against the real page in headless Chromium rather than a mock, which is
the lesson [[ISSUE-005]] left behind: 22 transcripts for this repo, one of them
already resumed, gives 21 rows under Earlier conversations, and clicking Resume
on a row landed on `/?session=<new id>` with the replayed conversation rendered.

The GTK sidebar is deliberately unchanged. It still lists live sessions only,
and the Resume dialog in the header is still how prior sessions are reached
across every folder at once; this issue was about one repo at a time.
