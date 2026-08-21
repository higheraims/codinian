---
id: ISSUE-006
title: WebKitGTK transcript pane and session kinds in the GUI
status: done
type: feature
area: gui
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-003, ISSUE-005]
---

## Summary

The desktop side of milestone M1. The sidebar lists both session kinds; the content pane
shows a WebKitGTK view of the shared transcript renderer for `sdk` sessions and the
existing VTE terminal for `terminal` sessions. Because the pane is just another client of
the local server, it needs no separate GTK marshalling for transcript content; only the
sidebar and its status dots update on the GTK main thread.

Also in scope: the new-session dialog (session_dialog.py) grows a kind selector and a
permission mode; the "Inject a prompt" box routes to `client.query` for `sdk` sessions and
keeps writing to the terminal for `terminal` ones; approvals resolve from inside the
embedded pane; and a native desktop notification fires when a session needs an approval or
finishes.

Milestone M1 is done when creating an SDK session in the desktop app streams structured
message and tool cards, inline approve and deny work, and VTE terminal sessions still run.

## Acceptance / done-when

- An `sdk` session selected in the sidebar shows a live transcript, not a terminal.
- A `terminal` session still shows VTE, with the current status dots.
- The new-session dialog sets kind and permission mode.
- Approving from the embedded pane unblocks the session.
- A desktop notification fires on pending-approval and on session-finished.

## Notes & worklog

- **Risk to check early:** whether the WebKitGTK / webkit2gtk typelib is present wherever
  codinian actually runs. If it is not, the fallback is native GTK transcript widgets for
  the desktop pane, and the shared renderer serves the browser only. Check this at the same
  time as the environment question in [[ISSUE-002]] rather than after the renderer is
  written.
- 2026-08-19: WebKit 6.0 was present, so the native-widget fallback was not needed.
- 2026-08-19: Live desktop run. The pane is served by our own server, which binds after the
  window opens, so a session created in that gap landed on a WebKit error page; a failed
  pane load now retries 10 times at 500ms.
- 2026-08-19: Desktop notifications added for `awaiting_approval`, a finished turn, and
  `error`. They are keyed per session so a session replaces rather than stacks its
  notifications, and suppressed when that session is already on screen. Clicking one fires
  the `app.focus-session` action, which selects the session and raises the window. Verified
  on the session bus, including the replacement behaviour and urgency.
- 2026-08-19: The app id used a domain that matches nothing else the author ships.
  Changed to `net.higheraims.codinian`, matching the author's other apps,
  with `packaging/net.higheraims.codinian.desktop` and
  `GLib.set_application_name("Codinian")` so notifications carry a name and icon instead of
  reading "main.py".
- 2026-08-19: The sidebar listed only sessions created through its own dialog, so sessions
  started from the browser were invisible. The window now adopts any `sdk` session it has
  no row for, without taking the current selection.

## Resolution

Done. Creating an SDK session from the + button streams a live transcript in the pane,
tool cards and approval cards render, Approve and Deny gate the call, terminal sessions
still run under VTE, and a notification fires on pending approval and on a finished turn.
