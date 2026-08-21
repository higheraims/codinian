---
id: ISSUE-009
title: Resume seeds the transcript from JSONL history
status: done
type: feature
area: sdk
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-002, ISSUE-003]
---

## Summary

SDK `resume` continues a conversation but does not re-emit past messages, so a resumed
session opens as an empty pane. The history is there as far as the model is concerned and
invisible as far as the user is concerned. The fix is to read it from disk: the `claude`
CLI already writes a JSONL transcript per session under `~/.claude/projects/*/*.jsonl`,
which is what `claude_history.py` (83 lines) reads today to build the resume picker.

Expand that into a full JSONL-to-`TranscriptEvent` parser, so resuming seeds the pane with
prior turns and then continues live via SDK resume. The parser is the same mapping problem
as the SDK driver's, from a different source, and both should land on the identical event
shapes from [[ISSUE-003]]; otherwise a resumed transcript renders differently from a live
one.

## Acceptance / done-when

- Resuming an `sdk` session shows the prior conversation before the first new message.
- Seeded events and live events render identically in the transcript.
- Transcript entries the parser does not recognize are skipped without losing the
  surrounding conversation.
- The resume picker (resume_dialog.py) keeps working for `terminal` sessions.

## Notes & worklog

- Worth confirming during [[ISSUE-002]] what the JSONL actually contains for tool calls and
  results, since that decides how much of a live transcript can be reconstructed.
- 2026-08-19: JSONL shapes confirmed against 52 real transcripts. Conversation lines are
  `assistant` (text, thinking, tool_use blocks) and `user` (tool_result and text blocks, or
  a plain string). Everything else is CLI bookkeeping: `attachment`, `mode`,
  `permission-mode`, `bridge-session`, `ai-title`, `last-prompt`, `queue-operation`,
  `file-history-snapshot`. Those are skipped by name, and any line type the parser does not
  recognise is skipped too, so an unfamiliar entry never costs the turns around it.
- 2026-08-19: **Thinking text is not recoverable.** Every `thinking` block in the JSONL
  carries a `signature` and an empty `thinking` string; across 350 blocks in 15 files, not
  one had text. So a resumed transcript shows the conversation and the tool calls but never
  the reasoning. The event shapes are identical to live ones, which is what the acceptance
  criterion asks, but this one kind of content simply is not on disk to replay.
- 2026-08-19: Sidechain entries (`isSidechain`) are subagent turns. Interleaved into the
  main thread they read as one confused conversation, so they are skipped; rendering them
  properly is [[ISSUE-017]]. Entries flagged `isMeta` are skipped as well.
- 2026-08-19: Seeding happens in `SdkRuntime.create` before the session starts, through the
  same `manager.add_event` path live events use, so history and live turns share one seq
  sequence and one render path. A conversation longer than `MAX_SEEDED_EVENTS` (2000) keeps
  its most recent events and says how many were dropped, rather than pushing tens of
  thousands of events into a pane.
- 2026-08-19: **The resume picker was creating terminal sessions.** `resume_dialog.py` built
  its `Session` without `kind`, so it defaulted to `terminal` and resume never reached the
  SDK path at all. It now has a "Resume as" selector defaulting to Agent SDK, which is what
  made this issue testable. The WebSocket `create` message also accepts a `resume` id now,
  so the browser can resume too.

## Resolution

Done. Verified live: resuming an old spike session opened the pane already showing the user
prompt, the assistant reply, a `Write` card marked error for the denied first attempt, the
retry marked done, and the closing summary, with the composer live to continue. Parsed
across four real transcripts of varying size, tool_use and tool_result paired with no
orphans on either side, and seq stayed contiguous through the seeded-then-live boundary.
