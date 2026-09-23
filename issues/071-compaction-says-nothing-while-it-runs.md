---
id: ISSUE-071
title: A compaction says nothing while it runs, and one took 137 seconds
status: open
type: feature
area: sdk
created: 2026-09-23
updated: 2026-09-23
related: [ISSUE-064, ISSUE-065, ISSUE-069]
---

## Summary

[[ISSUE-064]] draws a line in the transcript when a compaction has happened.
Nothing is drawn while one is happening, and `durationMs` on the two boundaries
recorded on this machine says that is 137 seconds in one case. For those two
minutes the session looks stalled: no text, no tool call, and a context figure
describing a conversation that is being replaced as it is read.

The CLI does announce it. `PreCompact` is a hook event the SDK declares beside
the `PreToolUse` and `PostToolUse` this app already registers, and it fires
before compaction with `trigger` saying whether a person asked or the CLI
decided.

## Acceptance / done-when

- A session being compacted says so for as long as it takes.
- The per-cycle state [[ISSUE-069]] keeps is cleared when compaction starts
  rather than when it ends.
- A compaction a person asked for and one the CLI decided on are distinguished,
  since `trigger` is in the payload either way.

## Notes & worklog

- 2026-09-23: Found while answering an open question in [[ISSUE-069]], which
  asked whether an announcement exists that would beat polling the context
  figure. It does, and it is no use for that: compaction starts as the hook
  returns, so there is no time to ask the session to write anything down. It is
  exactly the right size for saying what is going on.

- Measured rather than read off the types. A throwaway client on Haiku 4.5 with
  a `PreCompact` hook registered, sent `/compact`:

  ```
  PreCompact at +0.01s
    trigger: manual
    keys: custom_instructions, cwd, hook_event_name, prompt_id,
          session_id, transcript_path, trigger
  ```

- The wiring is already there. `sdk_session.py` registers two hooks and spells
  out a matcher for each, so a third costs a `HookMatcher` and a handler.
  Unlike `PreToolUse`, this one has nothing to wait for and should return at
  once: the hook holds compaction open while it runs.

- **Not verified: that it fires for `trigger: auto`.** Only the manual path is
  reachable without a conversation of a million tokens, which is the same wall
  [[ISSUE-064]] hit testing its own renderer. If it turns out to fire only for
  `manual`, this ticket is worth little, so that is the thing to establish
  first.

- No `compact_boundary` arrived before the turn's result in the run above, so
  what a manual compaction emits on the live wire is a second open question.
  Both boundaries in the stored transcripts here came from automatic ones.

## Resolution


