---
id: ISSUE-008
title: Browser client parity and approval concurrency
status: done
type: feature
area: remote
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-004, ISSUE-005, ISSUE-007, ISSUE-010]
---

## Summary

Bring the browser client up to what the embedded pane can do: live transcript, send a
message, approve or deny remotely. This is the away-from-desk workflow the pivot is for:
a session waiting on an approval should be resolvable from a phone.

That makes approval resolution a race. Two clients can answer the same
`approval_request`, a client can answer one that was already resolved, and a client can
answer one from a session that has since been restarted. Rule: first resolver wins;
later or stale resolutions are rejected and the client is told why, rather than silently
dropped. A restart cancels in-flight approvals and the session must be resumed, so say so in
the UI when it happens.

Add a notification hook for pending approvals so the phone case does not depend on the
user happening to look.

## Acceptance / done-when

- The browser can do everything the embedded pane can: read, send, approve, deny.
- Two clients racing on one approval produce one resolution and one clear rejection.
- Resolving an approval that no longer exists returns a message the UI can show.
- A pending approval triggers a notification.

## Notes & worklog

- Depends on token auth ([[ISSUE-007]]) being in place first. Approving tool calls from an
  unauthenticated browser is not a state to ship even briefly.
- 2026-08-19: Two acceptance items are already met, found while fixing [[ISSUE-005]].
  Resolving an approval that no longer exists returns `stale_or_unknown_request`, and
  `app.js` now shows that on the card and re-enables its buttons. First-resolver-wins is
  in `SdkSession.resolve`, which pops the future before setting it, and a deliberate
  duplicate resolve was rejected in a live run. Still open here: auth ([[ISSUE-007]]),
  a notification hook for the away-from-desk case, and saying in the UI that a restart
  cancelled an in-flight approval.
- 2026-08-19: Parity confirmed once auth landed ([[ISSUE-007]]). A browser with the token
  reads the live transcript, sends messages, and approves or denies, over the same
  WebSocket the desktop pane uses. The pane is that same page bundle, so parity is
  structural rather than something kept in step by hand.
- 2026-08-19: Browser notifications added for a pending approval. `Notification` is
  feature-detected, permission is asked once on a real gesture (selecting a session or
  sending a message) and never again after a denial, and the notification fires only while
  the page is hidden. Clicking it focuses the page and selects the session.
  **Two limits worth knowing.** The API only exists in a secure context, so on plain HTTP
  at a LAN address, which is the phone-on-the-wifi case this targets, `window.Notification`
  is undefined and this silently does nothing; over the SSH tunnel or on localhost it
  works. And a client only receives events for the session it is subscribed to, so an
  approval in another session raises nothing. The cross-session case is [[ISSUE-010]].
- 2026-08-19: The desktop pane is excluded from browser notifications. It reports
  `document.hidden` when its GTK stack page is not visible, so without the exclusion an
  approval would notify twice, once natively and once from inside the pane.
- 2026-08-19: A session that disappears from the `sessions` list while a client is
  watching it now says so: the transcript area explains that the session ended when
  Codinian restarted and that any approval waiting on a decision was cancelled. It fires
  only for a session that was actually being watched, not on first load or a normal switch.

## Resolution

Done. Verified against the running server in two independent browsers. Racing resolutions
produce one `approval_resolved` and one `stale_or_unknown_request`, which the losing client
shows on the card while re-enabling its buttons. Remaining notification gaps are recorded
above and belong to [[ISSUE-010]].
