---
id: ISSUE-077
title: A folded Thinking block hides most of what a session is doing
status: done
type: feature
area: remote
created: 2026-09-24
updated: 2026-09-24
related: [ISSUE-032, ISSUE-033]
---

## Summary

Reported by comparing two transcripts open in Codinian at once: one an SDK
session it started, one started with `claude` directly. The second reads as a
running commentary, short lines between the tool calls. The first is a column
of folded cards labelled THINKING and almost nothing else.

Both go through the same renderer. The difference is what the two sessions put
on the wire. Counting assistant content blocks in the two JSONL files:

|                 | Codinian SDK session | CLI-started session |
|-----------------|----------------------|---------------------|
| text blocks     | 9 (5,894 chars)      | 89, 75 under 300 chars (28,608 chars) |
| thinking blocks | 62, 61 non-empty (38,076 chars) | 130, all empty |

87% of the assistant's prose in the Codinian session was inside folded cards.
The CLI session has no Thinking cards at all: its 130 thinking blocks are empty
strings with a signature, because the CLI's default is `thinking.display:
omitted`, and `claude_history.py` skips a block whose text is blank. Codinian's
config has `thinking: summarized`, so it gets the reasoning and then hid it.

Whether the model writes preambles is not something this app controls. Whether
a transcript shows reasoning it already has, is.

## Acceptance / done-when

- A folded thinking block says what it is thinking about, not only that it is.
- The reader chooses between folded, folded with a preview, and open.
- The choice survives a transcript that drew before the setting arrived.

## Notes & worklog

- Not fixed here: thinking does not stream. `sdk_session.py` drops every delta
  that is not `text_delta`, and `agent_options.py` justifies it with "thinking
  and tool-argument deltas are 99% of the volume and neither is legible as it
  arrives". A probe against a live session disagrees on the first half:

  ```
  deltas: {'thinking_delta': 11, 'signature_delta': 1,
           'text_delta': 118, 'input_json_delta': 16}
  ```

  `thinking_delta` is prose and reads fine as it arrives; `input_json_delta` is
  the one that does not. So during a long thinking block the pane still shows
  nothing at all, then a card appears after the fact. Worth its own ticket.

## Resolution

A `thinking_view` setting: `collapsed`, `preview`, `expanded`, defaulting to
`preview`. It rides to the page on `/api/prefs` beside the footer preferences,
and appears in Settings as "Show thinking", in its own group rather than
alongside Thinking: one decides what the model returns, the other what the
transcript draws, and they share a word without sharing a meaning.

`buildThinkingBlock` always builds the hint from `firstMeaningfulLine`, so
switching the setting shows or hides it rather than rebuilding the transcript.
Only `expanded` touches the fold, since `open` is an attribute CSS cannot
reach, and it never closes a block the reader opened.

Checked in headless Chromium against a stubbed `/api/prefs`, two thinking
blocks either side of a tool card:

```
preview     open=false  hint shown
collapsed   open=false  hint hidden
expanded    open=true   hint hidden
```

With the prefs response held back 6 seconds, past the point where the
transcript had drawn: 0 blocks open before it landed, both open after.
