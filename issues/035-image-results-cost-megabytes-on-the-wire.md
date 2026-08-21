---
id: ISSUE-035
title: Image tool results carry their base64 through memory, the socket and every replay
status: open
type: chore
area: remote
created: 2026-08-21
updated: 2026-08-21
related: [ISSUE-026]
---

## Summary

A tool that returns a picture sends `{type: "image", source: {type: "base64",
media_type, data}}`, and `data` is the whole image. Codinian passes that block
through verbatim: `sdk_session.py` copies `block.content` into the event,
`SessionManager.add_event` keeps the event in memory for the life of the
session, the WebSocket broadcasts it, and a reconnecting client is sent the
whole backlog again. `claude_history.py` does the same on replay.

Measured on one real session (15 screenshots read during a UI review):

    GET /api/history/<sdk-id>  ->  2,647,253 bytes
    of which base64 image data ->  2,317,676 bytes   (88%)

That is one session's replay. The cost repeats per connected client and per
reconnect, and the in-memory event list holds the same bytes until the session
is closed.

Rendering the images is done (the transcript now shows them). This issue is
only about not carrying the bytes through the event stream to do it.

## Acceptance / done-when

- An image result reaches the client without the base64 riding inside the
  transcript event.
- Both clients still render the picture, live and on replay.
- A session that has read twenty screenshots does not hold twenty screenshots
  in `SessionManager._events`.

## Notes & worklog

- **2026-08-21, the shape of a fix.** The bytes already exist on disk in the
  CLI's own JSONL under `~/.claude/projects`, which is where replay reads from
  anyway. So the event could carry a reference -- session id, `tool_use_id`,
  block index -- and a route like `GET /api/sessions/{id}/image/{tool_use_id}/{n}`
  could serve the bytes, decoded, with a real `Content-Type` and a cacheable
  ETag. The browser then fetches each image once instead of receiving it inline
  on every reconnect.

  The live path needs somewhere to read from before the CLI has flushed its
  transcript. Either hold the bytes in a bounded side table keyed by the same
  reference (not in the event list), or write them to a per-session spool
  directory and serve from there.

- Whatever the mechanism, `docs/transcript-protocol.md` has to say what the
  event carries instead, since both clients render from that contract.
