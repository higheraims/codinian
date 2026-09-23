---
id: ISSUE-064
title: Compaction happens and the transcript says nothing
status: open
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
- The fall-through comment at `app.js:3643-3646` is right about `thinking_tokens`
  and wrong about this one. Fixing the general case by reading `content` as well
  as `message` risks surfacing other bookkeeping subtypes; naming
  `compact_boundary` explicitly is the safer half of this.

## Resolution
