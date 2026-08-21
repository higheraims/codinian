---
id: ISSUE-010
title: Cross-session approval inbox
status: done
type: feature
area: remote
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-004, ISSUE-008]
---

## Summary

With several sessions running, finding which one is blocked means clicking through the
sidebar. One queue of everything waiting on you, ordered by how long it has waited, turns
that into a single screen: useful on the desktop, necessary on a phone.

Each entry shows the session, the tool and its input, and approve/deny inline, so a queue
of five approvals is five taps rather than five navigations.

## Acceptance / done-when

- One view lists every pending approval across all sessions, with wait times.
- Approving from the inbox unblocks the right session, and the entry leaves the queue for
  every connected client.
- The inbox is usable at phone width.

## Notes & worklog

- Depends on the resolution rules in [[ISSUE-008]]: the inbox is the place where two
  clients are most likely to answer the same approval.
- 2026-08-19: Server side done. A client sends `subscribe_inbox` and gets back everything
  currently waiting, then receives every `approval_request` and `approval_resolved` from
  every session as `inbox_event`, each carrying the session meta so the client can name what
  is asking. `SdkSession` now remembers what each pending approval is, not just its future,
  so the inbox can list an approval raised before the client connected.
  Sent as its own message type rather than reusing `event`: a client watching one session
  updates its transcript from `event` and its inbox from `inbox_event`, and neither has to
  guess which one an event was meant for.
  Verified live: a second client that never subscribed to the session received the pending
  approval, resolved it, and saw the resolution. Remaining: the inbox UI in the browser.- 2026-08-19: Browser UI done. A count in the topbar opens a panel listing every waiting
  approval, each naming its session and tool and rendering the input through the same
  renderers as the transcript, so an Edit shows a diff and a plan shows a plan. Approve and
  Deny act in place, sending `resolve` with that entry's own `session_id`, so answering
  never means finding the session first. The panel closes and the button disables when the
  count reaches zero rather than showing an empty box, and it is absent in embed mode, where
  the desktop app already raises native notifications for a single session.

## Resolution

Done. Verified against the real backend: a genuine `approval_request` appeared in a
browser's inbox, was approved from the inbox without selecting that session, and the tool
then ran. Resolving from the transcript card instead removes only that entry from the
inbox, so `event` and `inbox_event` do not double-count. Not verified: the inbox row's own
rejection display in a real two-client race; the path is the one the approval card already
uses.
