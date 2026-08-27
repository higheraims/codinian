---
id: ISSUE-051
title: No way to send an image from the web client
status: open
type: feature
area: remote
created: 2026-08-27
updated: 2026-08-27
related: [ISSUE-035]
---

## Summary

There is no way to give Claude a picture from the browser. Reporting a display
problem on a phone means describing it in words, when a screenshot would say it
in one go, and the same gap blocks every other reason to show rather than tell:
a design mock, an error dialog from another app, a photo of a whiteboard.

Images currently travel one way. ISSUE-035 built the path for a tool result that
returns a picture, where the event carries a reference and the bytes are fetched
once over HTTP. Nothing carries a picture in the other direction.

## Acceptance / done-when

- A screenshot can be attached to a message from a phone browser, by file
  picker, by paste, and by drag and drop on a desktop.
- The image reaches Claude as part of that turn and it can describe what is in
  it.
- The transcript shows what was sent, not just the text beside it.
- Oversized images are refused with a message that says the limit, rather than
  failing somewhere further in.

## Notes & worklog

- The composer is a textarea, a Stop button and a Send button
  (`remote/static/index.html`). No file input, no paste handler, no drop target.
- The whole send path is typed to a string: the WebSocket takes
  `{t: "send", session_id, text}`, `SdkRuntime.send(session_id, text)` passes a
  `str`, and `SdkSession.send` queues onto `asyncio.Queue[str | None]`. Carrying
  an image means widening all of it, or sending the image out of band and
  referring to it from the text.
- Worth reusing rather than inventing: ISSUE-035's store already keys image
  bytes and serves them over an authenticated HTTP route, and its reasoning
  about keeping base64 out of the event stream applies in this direction too.
  An upload that put the bytes in the transcript event would replay them on
  every reconnect, which is the problem that issue exists to prevent.
- Size limits need a decision. A modern phone screenshot is 1 to 3 MB, and
  several of those in one conversation is the case ISSUE-035 measured at 2.6 MB
  of replay. Downscaling in the browser before upload is probably the cheaper
  answer than raising a server limit.
- The desktop GTK composer has the same gap. Worth doing in the same pass, since
  a screenshot is at least as likely there.
