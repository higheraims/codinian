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
- Cross-site scripting in the web client.
- The token leaking somewhere it should not (logs, the address bar, Referer
  headers).

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
