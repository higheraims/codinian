---
id: ISSUE-045
title: Pipe tables render as literal text in the transcript
status: done
type: bug
area: remote
created: 2026-08-27
updated: 2026-08-27
related: [ISSUE-013, ISSUE-026]
---

## Summary

Reported from a screenshot of a live session: a reply that contained a two-column
markdown table showed up in the transcript as the raw source, pipes and all, with
the `|---|---|` delimiter row sitting on its own line in the middle of the prose.

`renderMarkdown` in `remote/static/app.js` handled headings, lists, fenced code and
the inline rules, and everything else fell through to a paragraph. A table matched
nothing, so each row became a paragraph line. The issue-body preview in
`remote/static/project.js` already rendered pipe tables; the transcript renderer,
which is the one the assistant's own messages go through, did not.

## Acceptance / done-when

- A pipe table in a message, a thinking block or a plan renders as a real table.
- Cells keep their inline formatting: `code`, **bold**, links.
- Alignment from the delimiter row (`:---`, `:---:`, `---:`) is honoured.
- A wide table scrolls inside the message instead of stretching the bubble.
- Text that is not a table still reaches the reader verbatim.

## Notes & worklog

- Added a table block to `renderMarkdown`, built from DOM nodes like the rest of
  that function; no `innerHTML`, since the text comes off the wire.
- A table starts only where a row is followed by a delimiter row containing a pipe.
  A bare `---` under a paragraph is not enough, so ordinary prose is unaffected.
- Rows split on unescaped pipes only, so `a \| b` in a cell keeps the pipe as text.
- The header sets the column count; short rows are padded and long ones truncated.
- `consumeList` now stops at a line starting with `|`, so a table written directly
  under a list item is not swallowed as a wrapped continuation of that item.
- Cells override the `overflow-wrap: anywhere` that `.md-body` sets for long paths.
  In a narrow column that was splitting ordinary words ("count" became "coun / t").

## Resolution

Fixed in `remote/static/app.js` (renderer) and `remote/static/styles.css`
(`.md-table`, `.md-table-wrap`). Checked against the reported table plus alignment
rows, ragged rows, escaped pipes, a table with no trailing border pipe, a table
under a list item, and prose that only looks like one.
