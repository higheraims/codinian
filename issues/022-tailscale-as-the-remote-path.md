---
id: ISSUE-022
title: Tailscale as the remote path, including identity-based auth
status: done
type: feature
area: remote
created: 2026-08-19
updated: 2026-08-19
related: [ISSUE-007, ISSUE-008, ISSUE-010, ISSUE-020]
---

## Summary

The intended way to reach Codinian from a phone is a tailnet: connect to this
machine's MagicDNS name over HTTPS, with Tailscale issuing the certificate. That
is a better story than either option [[ISSUE-007]] shipped, and the app currently
knows nothing about it.

`tailscale serve --bg --https=443 8787` terminates TLS and proxies to the app,
which means the bind stays on `127.0.0.1` and the "Allow access from the local
network" switch stays off. Nothing is exposed to the LAN, no certificate is
managed by hand, and the traffic is encrypted, which the plain-HTTP LAN bind
never was.

Two things follow immediately:

- **Browser notifications start working on the phone.** The Notification API
  needs a secure context. Over `https://<host>.<tailnet>.ts.net` the page is
  secure, so the pending-approval notification from [[ISSUE-008]] works, where
  over plain HTTP at a LAN address it silently does nothing.
- **The token stops being the only credential.** Behind `tailscale serve`,
  requests arrive with `Tailscale-User-Login`, `Tailscale-User-Name` and
  `Tailscale-User-Profile-Pic` identifying the tailnet user. That is real
  identity, checked by Tailscale, rather than a shared secret pasted into a URL.

## Acceptance / done-when

- The Remote Access dialog detects a `tailscale serve` config and shows the
  tailnet URL alongside the loopback one, rather than the user assembling it.
- Requests carrying Tailscale identity headers are accepted without a token, and
  the app records which tailnet user approved a tool call.
- Identity headers are trusted only when the request came from the local proxy,
  never from an arbitrary client that set the header itself.
- `docs/remote-access.md` recommends the tailnet path first, with the SSH tunnel
  as the fallback and the LAN switch as the last resort.

## Notes & worklog

- **The header-trust rule is the whole security question here.** `Tailscale-User-*`
  headers are only meaningful because `tailscale serve` sets them on the way
  through; anything else can forge them. Accept them only on connections from
  loopback, and only when serve is actually configured. Get this wrong and the
  app is less safe than the token it replaced, not more.
- **Do not use `tailscale funnel`.** Funnel publishes to the open internet, and
  approving a tool call over this socket runs code on this machine. The tailnet
  is the boundary that makes remote approval reasonable.
- `tailscale status --json` and the LocalAPI give the app the node's DNS name and
  the state of the tailnet without shelling out to parse human output.
- Longer thought, not for this issue: with several machines on the tailnet each
  running Codinian, one browser could show sessions across all of them. That is
  a fleet view, and it belongs with the project workspace track ([[ISSUE-020]])
  rather than here.
- `tailscale cert` is the alternative if serving TLS from aiohttp directly ever
  looks better than proxying through `serve`. It means renewing and reloading
  certificates in-process, which `serve` handles for free.
- 2026-08-19: `tailscale serve --bg --https=443 8787` needed no root on this machine and
  worked immediately, giving `https://<machine>.<tailnet>.ts.net/` with a real
  Let's Encrypt certificate. Confirmed over that URL: static assets load, `/api/` with the
  token returns 200, a browser-shaped `Origin` passes the same-origin check, and a
  cross-origin request still gets 403.
- 2026-08-19: **The header-trust rule this issue proposed does not hold, and testing is the
  only reason we know.** The plan was to trust identity headers on loopback connections.
  Measured against a real proxied request, `tailscale serve` connects from **127.0.0.1**,
  the same address as any local process, and carries `Tailscale-User-Login`,
  `Tailscale-User-Name`, `Tailscale-User-Profile-Pic`, `Tailscale-Headers-Info` and
  `X-Forwarded-*`. Every one of those is an ordinary header a local client can set. A
  proxied request and a forged local one are indistinguishable at the socket. Demonstrated:
  with trust enabled, `curl` from this machine sending a made-up login header and nothing
  else was authenticated exactly as the real proxied request was.
- 2026-08-19: So the acceptance item "requests carrying Tailscale identity headers are
  accepted without a token" ships as an **opt-in that defaults to off**, rather than as the
  behaviour. Turning it on trades a secret only the owner can read (a 0600 file) for a
  header anything on the machine can type, so it is weaker than what it replaces even with
  the guards applied: the setting on, the bind on loopback, the request from loopback, and
  `tailscale serve` actually proxying this port. It is changed by editing `config.json` and
  restarting; there is deliberately no switch in the app, since a one-tap control invites
  turning it on without reading why it is off.
- 2026-08-19: Identity is used for attribution unconditionally, which is the part that is
  sound. Resolving an approval from the tailnet stamps `approval_resolved` with
  `decided_by`, and the transcript shows it. Verified end to end over `wss://` on the
  tailnet: an approval raised, resolved from that connection, and recorded as
  `decided_by: "someone@github"`, with the tool then running.
- 2026-08-19: `tailscale.py` shells out to `tailscale status --json` and
  `tailscale serve status --json` with a 15 second cache, so the middleware does not spawn a
  process per request, and treats every failure (no binary, not running, timeout, bad JSON)
  as "cannot say anything about Tailscale" rather than as an error.
- 2026-08-19: The Remote Access dialog shows the tailnet URL with the token attached beside
  the loopback one, and when serve is not configured it prints the exact command to run.

## Resolution

Done, with one deliberate departure from the acceptance list recorded above: identity
authentication exists but defaults to off, because measurement showed it cannot be made as
strong as the token it would replace. Everything else holds: the dialog surfaces the tailnet
URL, approvals record the tailnet user who answered, and `docs/remote-access.md` recommends
the tailnet first, the SSH tunnel second and the LAN switch last, with the funnel warning
and the reason the trust setting stays off.
