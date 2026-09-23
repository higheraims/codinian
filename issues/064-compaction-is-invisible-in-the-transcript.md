---
id: ISSUE-064
title: Compaction happens and the transcript says nothing
status: done
type: bug
area: remote
created: 2026-09-23
updated: 2026-09-23
related: [ISSUE-065]
---

## Summary

A Codinian session can auto-compact, replacing the conversation with a summary,
and no client shows a thing. The event arrives; the renderer drops it.

Session `79ab13a7` ("FSO Description field before Contacts", `nehemiah`) compacted
on 2026-09-16. From its transcript,
`~/.claude/projects/-home-ntyler-Projects-nehemiah/89e4a4d3-c068-4840-bc29-a3e8c77be6a0.jsonl`:

```json
{"type":"system","subtype":"compact_boundary","content":"Conversation compacted",
 "compactMetadata":{"trigger":"auto","preTokens":1000607,"durationMs":136891}}
```

`sdk_session.py:1096` forwards every `SystemMessage` to the bus, so the event
reaches the browser as `system`/`compact_boundary`. `buildSystemLine`
(`app.js:3630-3650`) names `init`, `error` and `permission_mode`, then falls
through to `ev.data.message || ''`. This record carries its text under
`content`, so `text` is empty, the function returns `null`, and `app.js:2762`
drops null lines on purpose. Nothing is drawn.

## Acceptance / done-when

- A `compact_boundary` draws a system line naming the trigger and the
  pre-compaction token count, so a reader can tell a summary from the
  conversation it replaced.
- The same line appears on replay, not only live. Check that
  `claude_history.py` passes the record through rather than filtering it.
- A system subtype that carries `content` but no `message` is no longer dropped
  silently. The fall-through should read both.

## Notes & worklog

- 2026-09-23: Found while answering why Codinian has no compaction indicator
  where the VSCodium plugin does. Two separate problems; the advance warning is
  [[ISSUE-065]] and this is the after-the-fact marker.
- **Rare but expensive.** Exactly two `compact_boundary` records exist across
  every transcript on this machine. Both were at roughly 1,000,000 tokens, and
  the one above took 137 seconds. So it is not a daily event, but when it
  happens a million tokens of context becomes a summary with no notice.
- **It was broken twice, not once.** The renderer dropping the line was the
  visible half. `_events_from` in `claude_history.py` only ever built events
  from `assistant` and `user` records, so on replay the boundary was not
  dropped at the last step; it never became an event at all. Both are fixed:
  the parser emits `compact_boundary` by name, and the renderer draws it by
  name.
- The general fall-through is unchanged. `docs/transcript-protocol.md` states
  that an unknown subtype shows `data.message` or nothing, and widening it to
  read `content` as well would surface the bookkeeping subtypes that rule
  exists to hide. Only this subtype is named.
- **Both spellings are read.** The stored JSONL writes `compactMetadata` and
  `preTokens`. What the CLI puts on the live wire has not been seen here, and
  waiting for a session to cross a million tokens to find out is not a test.
  Guessing wrong on the live path would have stayed hidden for months.
- Verified against the real record: replaying
  `89e4a4d3-c068-4840-bc29-a3e8c77be6a0` now yields a `compact_boundary` event
  at index 1199 carrying `trigger: auto` and `preTokens: 1000607`. The mock
  gained a case in session `b2f9013c` so the renderer can be exercised without
  a million-token session, and it draws:

  ```
  Conversation compacted (auto, 1,000,607 tokens).
  What follows continues from a summary of everything above.
  ```

## Resolution

Named in three places: the parser that builds replay events, the renderer, and
the protocol doc. The mock carries a case so it stays checkable.
