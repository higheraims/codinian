# Remote access

Codinian runs a small HTTP and WebSocket server alongside the GTK window, so a
session can be read and driven from a browser on this machine, or from a phone
or laptop elsewhere. The desktop app's own transcript pane is a client of this
same server, so anything the pane can do, a browser can do.

## Port, bind and token

All three live in `~/.config/codinian/config.json`, written 0600 on first run:

```json
{
  "token": "<43 random url-safe characters>",
  "bind": "127.0.0.1",
  "port": 8787
}
```

The server binds **loopback only** by default. Approving a tool call over this
socket runs whatever that tool does on this machine, so a reachable port is a
remote shell with a button, and the default is the safe one.

The **token** is generated on first run and required by every `/api/` request.
There is no password and no user account; the token is the credential.

These controls are deliberately not in the browser client, which is otherwise
able to do everything the desktop pane can. They set the token and the bind: a
token holder who could flip the bind from a phone would be opening this
machine's port to the whole local network, so the controls stay on the machine
they affect (ISSUE-030).

## Getting the link

In the desktop app, the cogwheel at the bottom of the sidebar opens **Settings**;
the link lives on its **Remote access** tab (ISSUE-030 moved it there from a
dialog behind a toolbar button). It shows the full URL with the token in it,
with a button to copy it:

```
http://127.0.0.1:8787/?token=<token>
```

Open that once in a browser. The page saves the token in `localStorage` and
removes it from the address bar, so later visits to `http://127.0.0.1:8787/`
work without it. Clearing site data means fetching the link again.

Under the links is a **QR code** (ISSUE-029) for the link a phone can actually
use, which is not the one at the top: `127.0.0.1` on a phone is the phone. It
encodes the tailnet link when `tailscale serve` is proxying the port, otherwise
the LAN address if the bind is open, otherwise nothing, with a caption saying
which of those it is. It follows the token, so rotating redraws it. Treat a
photograph of the code the way you would treat the link, because it is the link.

Drawing it needs `python3-qrcode`. Without that package the tab says so and
the links still work.

The same tab has the LAN switch, the Tailscale identity switch described
below, and a **Rotate** button. Rotating takes
effect on the next request, with no restart: every browser holding the old link
is cut off, and the desktop app reloads its own panes with the new token.

## Reaching another machine

### Tailscale, the recommended path

`tailscale serve` terminates TLS at this machine's MagicDNS name and proxies to
the app on loopback:

```
tailscale serve --bg --https=443 8787
```

That prints the URL, for example
`https://<machine>.<tailnet>.ts.net/`. The Remote access tab picks
the configuration up and shows the same URL with the token attached, ready to
copy to a phone. To undo it: `tailscale serve --https=443 off`.

This is better than every other option here on each count that matters. The bind
stays on loopback, so nothing is exposed to the local network. The certificate is
Tailscale's problem, not yours. The traffic is encrypted, which the LAN bind
never was. And because the page is served over HTTPS it counts as a secure
context, so browser notifications for a pending approval work on a phone, which
they do not over plain HTTP at a LAN address.

**Do not use `tailscale funnel`.** Funnel publishes to the open internet.
Approving a tool call over this socket runs code on this machine, and the tailnet
boundary is what makes remote approval reasonable at all.

### SSH tunnel

Works anywhere, needs no change to the bind, and leaves the port closed:

```
ssh -L 8787:localhost:8787 user@codinian-host
```

Then open `http://localhost:8787/?token=<token>` on the machine you ran `ssh`
from. Being on localhost, this is a secure context too, so notifications work.

### The LAN switch, last resort

**Allow access from the local network** changes `bind` to `0.0.0.0` and takes
effect when Codinian restarts. Then any device on the same network segment can
reach the port, the token is the only thing between them and your sessions, and
the traffic is plain HTTP, so anyone who can watch the network sees the token go
past. Prefer either option above.

## HTTP API

Everything under `/api/` requires the token, as either an
`Authorization: Bearer <token>` header or a `?token=<token>` query parameter.
The query form exists because the browser WebSocket API cannot set headers.
Static assets (the HTML, CSS and JS) are served without a token; they are the
same files for everybody and hold no session data.

| Method | Path                        | Description                                       |
|--------|-----------------------------|---------------------------------------------------|
| GET    | `/`                         | The web UI (`remote/static/index.html`)           |
| GET    | `/api/auth`                 | `{"ok": true}`, or 401. Lets a client check a token |
| GET    | `/api/ws`                   | The transcript WebSocket (see below)              |
| GET    | `/api/sessions`             | List all sessions as JSON                         |
| GET    | `/api/sessions/{id}`        | One session, 404 if unknown                       |
| GET    | `/api/sessions/{id}/output` | A terminal session's screen as HTML                |
| POST   | `/api/sessions/{id}/inject` | Send text to a terminal session, as if typed       |

A request without a token gets `401 {"error": "unauthorized"}`. A request
carrying an `Origin` header from a different host gets
`403 {"error": "bad_origin"}`, so a page on another site cannot drive the API
using a token it somehow obtained.

```
curl -H "Authorization: Bearer $TOKEN" http://localhost:8787/api/sessions
```

The `output` and `inject` endpoints belong to the older terminal-mirror path
and act on `terminal` sessions. SDK sessions are driven over the WebSocket.

## WebSocket

`/api/ws` speaks the event protocol in
[transcript-protocol.md](transcript-protocol.md): it streams `TranscriptEvent`s
for subscribed sessions and accepts `subscribe`, `unsubscribe`, `send`,
`resolve` and `create`. This is the interesting surface. `resolve` is how a tool
call gets approved or denied, from the desktop pane or from a phone.

Connect with the token in the query string:

```
ws://localhost:8787/api/ws?token=<token>
```

Errors come back as `{"t": "error", "error": "<code>"}`. Current codes:
`stale_or_unknown_request` for a `resolve` naming an approval that is no longer
pending, `session_start_failed` when a `create` could not start (the `detail`
field says why), and `request_failed` for anything else that went wrong
handling a message. None of them close the socket.

## Approvals, restarts and races

First resolver wins. An approval is a suspended point inside a running turn,
held by a future that is popped before it is completed, so two clients
answering at once produce one decision and one `stale_or_unknown_request` for
the loser. The rejected client re-enables its buttons and shows why rather than
sitting on a dimmed card.

**Quitting Codinian cancels any in-flight approval.** Sessions live in memory;
closing the app drops the pending future along with them. The turn does not
resume on its own, and the session has to be started again. A browser that was
watching that session says so when it reconnects and finds the session gone.

## Tailscale identity

A request arriving through `tailscale serve` carries headers naming the tailnet
user:

```
Tailscale-User-Login: someone@example.com
Tailscale-User-Name: someone
Tailscale-User-Profile-Pic: https://...
Tailscale-Headers-Info: https://tailscale.com/s/serve-headers
X-Forwarded-For: 100.x.y.z
X-Forwarded-Proto: https
```

Codinian records that identity on approvals. Answering from the tailnet stamps
the `approval_resolved` event with `decided_by`, and the transcript shows who
approved a tool call, which is worth having as soon as more than one person or
device can answer.

### Why identity does not replace the token by default

There is a setting, `trust_tailscale_identity`, that accepts these headers
instead of the token. It is **off**, and it should usually stay off. It sits on
the Remote access tab in Settings, and in `config.json` under the same name;
until ISSUE-030 it was only reachable by editing that file, which is a poor
hiding place for a security decision. Changing it applies to the next request,
with no restart.

`tailscale serve` connects to the app from **127.0.0.1**, exactly like any other
local process, and every one of those headers is an ordinary HTTP header that
any local process can set. A proxied request and a forged one are
indistinguishable at the socket. This was measured, not assumed: with the setting
turned on, `curl` from this machine sending nothing but a made-up
`Tailscale-User-Login` and `Tailscale-Headers-Info` was authenticated exactly as
a real proxied request was.

So turning it on trades a secret only the file owner can read, in a 0600 file,
for a header anything on the machine can type. Even with the guards Codinian
applies (the bind must be loopback, the request must come from loopback, and
`tailscale serve` must actually be proxying this port), it is weaker than the
token. It is worth having only if you would rather not paste a token at all and
you accept that any process on this machine can then drive your sessions.

Changing it means editing `config.json` and restarting Codinian. There is no
switch in the app, on purpose.

## What the token does not protect

- The traffic is plain HTTP. On a LAN bind, the token crosses the network in
  the clear, in the URL of every request. There is no TLS.
- Anyone holding the token can read every session's transcript, including
  whatever your working directories and tool output contain, and can approve
  tool calls. Treat the link like an SSH key.
- Rotating the token is the revocation mechanism. There is no per-device
  access and no audit of who used it.
