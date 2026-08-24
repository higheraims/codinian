# Security

Codinian opens a local network port and, when you approve a tool call over it,
runs that tool on your machine. That makes its remote surface worth reporting
bugs against carefully.

## Reporting a vulnerability

Please report privately rather than opening a public issue. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository (the **Security** tab, "Report a vulnerability"). Include what
you did, what happened, and how to reproduce it.

Expect an acknowledgement within a week. This is a small 0.1 project maintained
in spare time, so please allow time for a fix before disclosing publicly.

## What is in scope

- Auth bypass on the remote server: reaching any `/api/` route or the WebSocket
  without the token.
- Reading or writing outside a registered project through the files or issues
  API (path traversal).
- Command or argument injection through the git or file endpoints.
- Cross-site scripting in the web client. The image route
  (`/api/sessions/{id}/image/...` and its history twin) is worth a look: it
  serves bytes that came out of a tool result from our own origin. The media
  type is matched against `image/...` rather than echoed, and the response
  carries `nosniff` and `default-src 'none'; sandbox`.
- The token leaking somewhere it should not (logs, the address bar, Referer
  headers). One deliberate exception is listed below.

## Known and documented limits

These are design constraints, not vulnerabilities, and are covered in
[docs/remote-access.md](docs/remote-access.md):

- A plain LAN bind serves over HTTP with no TLS; the token crosses the network in
  the clear. The Tailscale path exists to avoid this.
- Anyone holding the token can read every transcript and approve tool calls.
  Rotating the token is the only revocation mechanism; there is no per-device
  access or audit log.
- The `trust_tailscale_identity` setting, off by default, accepts identity
  headers in place of the token and is weaker than the token. Its trade-off is
  documented in the same file.
- The `Origin` check that answers `bad_origin` is a second lock, not the
  boundary. Both headers it compares come from the caller and stay consistent
  under DNS rebinding, so the token is what actually protects a state-changing
  route.
- **The token appears in one kind of URL.** A tool result that returns a picture
  is rendered by fetching it, and an `img` element cannot send an
  `Authorization` header, so those URLs carry `?token=` in the same way the
  WebSocket URL always has. Four things bound it: the requests are same-origin,
  the elements carry `referrerpolicy="no-referrer"`, the server runs with its
  access log off, and the page moves the token out of the address bar into
  `localStorage` on first load. What is not bounded is anything proxying in
  front of the app, which sees the full request URL; if you put one there, its
  logs are where this would surface.
