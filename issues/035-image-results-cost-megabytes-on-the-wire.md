---
id: ISSUE-035
title: Image tool results carry their base64 through memory, the socket and every replay
status: done
type: chore
area: remote
created: 2026-08-21
updated: 2026-08-24
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

## Resolution

Built the way the worklog sketched it: the event carries a reference, a route
serves the bytes, and there are two places the bytes can come from.

**The wire.** `images.dereference_content` rewrites an image block's source from
`{type: "base64", media_type, data}` to
`{type: "codinian_ref", media_type, bytes, path}`, where `path` is a complete
same-origin URL and `bytes` is the decoded size. Content holding no image is
returned as the same object, so an ordinary text result costs one `isinstance`
and a loop that finds nothing. An image whose `tool_use_id` will not go in a URL
is left inline rather than turned into a link to nothing.
`docs/transcript-protocol.md` carries the new shape, since both clients render
from that contract.

**Two routes, and the client picks neither.** The backend builds the path, so a
live session's images resolve through
`/api/sessions/{id}/image/{tool_use_id}/{n}` and replayed ones through
`/api/history/{sdk_id}/image/{tool_use_id}/{n}`, the latter taking `?agent=` for
a subagent's transcript. A client fetches the path it was given. Both answer with
a real `Content-Type`, an ETag over the bytes, `nosniff`, a locked-down CSP, and
`Cache-Control: private, max-age=31536000, immutable`, because a tool result
never changes and the browser should not come back at all.

**Two sources, because one is not enough.** `images.store` is a bounded
in-memory dict keyed by session, tool call and block index, capped at 32 MiB and
evicted oldest first, filled by the live path and dropped when a session closes.
It is not the durable copy; it exists to cover the window before the CLI has
flushed its own transcript. Behind it, `claude_history.image_from_transcript`
resolves the same reference against the JSONL under `~/.claude/projects`, which
holds the same base64 keyed the same way. The session route tries the store then
the file, so an image survives eviction; the history route reads the file only,
so a conversation with no live session behind it still renders.

The token rides in the img URL's query string, which `apiFetch` deliberately
avoids. There is no alternative: an `img` cannot carry an Authorization header,
which is the same reason `wsUrl` does it. `imageRefSrc` refuses any path that is
not one of ours, so a reference out of a tool result cannot aim the fetch, token
attached, somewhere else. The images are lazy-loaded and carry
`referrerpolicy="no-referrer"`, and a reference that no longer resolves is
replaced with "This image is no longer available" rather than a broken-image
icon.

**Measured.** On the largest local transcript containing images, replay went from
7,121,209 bytes to 2,179,949, with 4,941,260 bytes of base64 no longer in the
payload. On the live path, a `Read` of a 7,831-byte PNG produced a `tool_result`
frame of 371 bytes; before, that frame carried the whole 10,444-character base64.
The store, not `SessionManager._events`, is what holds the bytes, and it is
capped.

**Verified in the browser, not just on the wire.** A real session in a real
instance, read through headless Chromium: the pane shows one
`img.tool-result-image` whose `src` is the reference path,
`naturalWidth`/`naturalHeight` 64 by 64, no missing-image note. The same
conversation opened through `?history=<sdk-id>` renders the same picture from the
history route. Fetching the path directly returns bytes identical to the source
PNG (`cmp` clean), answers 304 to a matching `If-None-Match`, 401 without a
token, and 403 to a cross-origin request.
