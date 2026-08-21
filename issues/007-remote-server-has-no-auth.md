---
id: ISSUE-007
title: Remote server has no auth and binds every interface
status: done
type: bug
area: remote
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-005, ISSUE-008]
---

## Summary

`REMOTE_PORT = 8787` in main.py binds `0.0.0.0` on every run, with no authentication, no
TLS, and no setting to turn it off. Anyone who can reach the port can list sessions, read
working directories and full terminal output, and inject arbitrary text into a live
`claude` process with whatever tool permissions that session holds. `docs/remote-access.md`
documents this honestly and recommends an SSH tunnel, but a documented hole is still a
hole, and once [[ISSUE-005]] lets a client approve tool calls, an unauthenticated port
becomes remote code execution with a button.

Fix: generate a token on first run, store it in config, show it in the GTK app so it can
be copied to a phone or another machine, and require it on every API call and WebSocket
connection. Bind localhost by default; binding to the LAN becomes an explicit opt-in the
user chooses, not the default they inherit.

## Acceptance / done-when

- A token is generated on first run, persisted, and visible in the desktop app.
- Every REST endpoint and the WebSocket reject requests without a valid token.
- The default bind is `127.0.0.1`; reaching the LAN takes a deliberate setting.
- `docs/remote-access.md` is rewritten to match, including where the token lives and how to
  rotate it.

## Notes & worklog

- The SSH-tunnel path already documented in `remote-access.md` stays the recommendation for
  reaching another machine; the token protects the case where the port is exposed anyway.
- 2026-08-19: The `0.0.0.0` half was already fixed during M1; the bind moved to
  `127.0.0.1` when the WebSocket landed, since approving a tool call over it is remote
  code execution. This issue closes the rest.
- 2026-08-19: Auth implemented. New `config.py` owns `~/.config/codinian/config.json`
  (written 0600) with `token`, `bind` and `port`. The token is 32 random url-safe bytes,
  generated on first run. An aiohttp middleware gates every `/api/` path and answers 401
  `unauthorized`; static assets stay open, since they are the same files for everybody,
  carry no session data, and have to load before the page can present a token. The token
  is read from `Authorization: Bearer`, `X-Codinian-Token`, or a `token` query parameter,
  compared with `secrets.compare_digest`. The query form is not a shortcut: the browser
  WebSocket API cannot set headers.
- 2026-08-19: Added an origin check answering 403 `bad_origin`, so a page on another site
  cannot drive the API with a token it obtained. Requests with no `Origin` header (curl,
  non-browser clients) pass.
- 2026-08-19: `GET /api/auth` exists so a client can tell a wrong token apart from an
  absent server. Without it the browser sees the same failed handshake either way and
  cannot say which happened.
- 2026-08-19: The middleware reads the token from the shared config dict per request
  rather than closing over it, so rotating from the desktop app takes effect on the next
  request with no restart. Verified: the old token goes 200 to 401 the moment the dict
  changes, on both REST and the WebSocket. The desktop app reloads its own transcript
  panes with the new token, since each pane holds the old one in its URL.
- 2026-08-19: `remote_dialog.py` adds the Remote Access dialog, reachable from the server
  icon in the sidebar header: the URL with the token in it and a copy button, a switch for
  the LAN bind (writes config, applies on restart), and a rotate button.

## Resolution

Done. The token is generated on first run, persisted 0600, and visible in the desktop app.
Verified against the running server: static assets load without a token, `/api/` returns 401
without one and 200 with either accepted form, a cross-origin request gets 403, and the
WebSocket handshake is refused with 401 unless the token is in the query string. The bind
defaults to loopback and reaching the LAN is a switch the user throws. `docs/remote-access.md`
is rewritten around the token, the tunnel, and what the token does not protect.
