---
id: ISSUE-015
title: Searchable transcript history browser
status: done
type: feature
area: gui
created: 2026-08-16
updated: 2026-08-16
related: [ISSUE-009]
---

## Summary

The resume picker shows a session's first message and how long ago it was touched, which is
enough to recognize a session you remember and useless for finding one you do not. Once
[[ISSUE-009]] can parse `~/.claude/projects/*/*.jsonl` into events, the same parser can
back a search across every past session: find the conversation where a file was edited, or
where a particular error appeared, and read it without resuming it.

Backlog item from the M4 list. Reading a past transcript is a separate act from continuing
it, and conflating the two is why the resume picker is a poor search tool.

## Acceptance / done-when

- Full-text search across stored transcripts, with the working directory and date shown per
  hit.
- A hit opens the transcript read-only, using the same renderer as a live session.
- Resuming from a search result is available but not the only action.

## Notes & worklog

- Search over thousands of JSONL files needs an index rather than a scan per query. Decide
  whether the existing SQLite database holds it.

## Resolution

### No index, and the worklog note above was wrong about why

That note assumed "thousands of JSONL files" needing an index, and wondered
whether the SQLite database should hold it. Measured before building anything:
**65 transcripts, 131 MiB**, and a full scan answers a query in **155 to 485 ms**
depending on how many files contain the needle. That is inside the range where a
search box feels immediate, so there is no index, no build step, no staleness
question, and no second copy of the user's conversations on disk.

The scan is only fast because it does not parse. A raw substring test over each
file's bytes rejects most files outright, and JSON is parsed only for the lines
that actually matched. Parsing 131 MiB of JSON per query would not be viable, so
the shape of the optimisation matters more than the fact of it.

`history_search.py` records the threshold for revisiting: roughly when a scan
stops answering inside a second, which at the current ratio is somewhere north
of 400 MiB.

### The three acceptance criteria

**Full-text search with working directory and date per hit.** `GET
/api/history/search`, newest first, capped at 50 sessions and 5 snippets each so
a query like `e` cannot return more than the answer. Snippets are excerpts
around the match, and each is labelled for what the content actually is rather
than by the transcript's role field: the CLI records a tool result as a `user`
message, so labelling by role put "USER" beside a wall of command output. The
first screenshot showed exactly that and the labels were reworked.

**A hit opens read-only in the same renderer.** `index.html?history=<uuid>`
fetches the events and feeds them through `appendEventToDom`, the same function
a live event takes. The composer is disabled and says why, because one that
silently does nothing reads as broken. Verified on the real page: 103 entries,
93 tool cards with their summaries, and the status reading "stored transcript".

**Resume is available but not the only action.** Each hit offers Read and
Resume. `POST /api/history/{id}/resume` is deliberately separate from the
project resume route: that one refuses any id its project does not own, which is
the right guard when a project id is in the URL and the wrong one here, where a
hit can live in a folder that was never registered as a project. The other
guards carry over, including handing back an already-open conversation rather
than starting a second session on the same transcript.

### Surfaces

A `history.html` page, reachable in the browser and opened in the desktop by a
**History** row in the sidebar's pinned bottom group, beside Settings. A query
lives in the URL, so a search is a link worth keeping.

Subagent transcripts are excluded from results. Their text is the parent's work
seen from inside, so including them would report one conversation twice, and the
second id is not one the resume path can open.

### One finding worth keeping

The corpus includes the session doing the searching. A test asserting that a
hardcoded nonsense string returns nothing **fails**, because writing the test
writes the string into the running session's own transcript, which the search
then finds. The test now builds its needle at run time and never prints it.

### What was checked

Search: empty and whitespace queries, a string that exists nowhere, ordering,
every hit carrying a cwd and snippets, every snippet actually containing the
needle, case-insensitivity agreeing with the base query, the snippet and session
caps holding under a needle matching thousands of times, subagent transcripts
absent from results, and scoping to one project narrowing them.

Routes: search and read-only transcript against the running server. On the real
page: 7 hits rendered with highlighted excerpts, cwd, relative date and both
actions, with the token stripped from the address bar. On a built window: the
History row above Settings, its pane built on first selection, the header title,
and switching back to Settings.
