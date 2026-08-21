---
id: ISSUE-043
title: Container version
status: open
type: feature
area: packaging
created: 2026-08-21
updated: 2026-08-21
related: [ISSUE-022, ISSUE-029, ISSUE-030]
---

## Summary

Run Codinian as a headless container: the aiohttp server as the whole
application, no GTK, no display, reached from a browser over the tailnet or
from the API directly.

Most of this already exists. Seven of the 29 Python modules import `gi`:
`main.py`, `window.py`, `settings_view.py`, `session_dialog.py`,
`resume_dialog.py`, `remote_panel.py` and `theme.py`. Nothing the server touches
does. `run_server` (`remote/server.py:474`) takes `(manager, runtime, config)`,
binds the asyncio loop into the runtime, wires the event bus to the WebSocket
hub, and ends in `await asyncio.Event().wait()`; it is already written to be the
top of a process rather than a helper of a window. `docs/remote-access.md` states
the intent this issue cashes in: anything the desktop pane can do, a browser can
do.

The payoff is not packaging. It is that `bypassPermissions` and `auto` today mean
an agent with the run of the home directory, and in a container with narrow
mounts they stop being reckless.

## Acceptance / done-when

- `serve.py` starts the server with no GTK import: open the database, subscribe
  the persist callback, install the project resolver via
  `project.cached_resolver()`, then `asyncio.run(run_server(...))`. It is the
  four non-GTK lines of `main.py:76-101`, reusing `db.open_db` and
  `db.save_session` rather than reimplementing persistence.
- `pyproject.toml` exposes it as a `codinian-serve` console script.
- `config.load()` accepts `CODINIAN_TOKEN`, `CODINIAN_BIND` and
  `CODINIAN_PORT`, so a container can be handed a token from a secret with no
  config file. With no token supplied, first-run generation stays as it is and
  the startup line prints the URL, because there is no Settings tab to read it
  from. `CLAUDIUS_CONFIG` gets renamed to `CODIGULUS_CONFIG` while this file is
  open, keeping the old name working.
- A `Containerfile` builds an image with Python 3.12+, `aiohttp`,
  `claude-agent-sdk`, `qrcode`, Node with `@anthropic-ai/claude-code`, and `git`,
  running as a non-root user whose uid can be set at build time to match the host
  owner of the mounted work trees.
- Documented volumes: `~/.claude` (credentials, and the JSONL transcripts that
  `claude_history.py:21` reads for history search and resume),
  `~/.config/codinian` (token, bind, agent settings), `~/.local/share/codinian`
  (`codinian.db`, `projects.json`, `templates.json`), and the project trees the
  agent is allowed to touch.
- `files.open_external` returns 501 rather than spawning `xdg-open` when there is
  no display to open onto.
- `docs/remote-access.md` gains a section on the container: what to mount, how
  authentication works, and why the published port stays behind
  `tailscale serve` and never `tailscale funnel`.
- A rootless podman quadlet unit in `packaging/`, so an always-on machine starts
  it at boot with no desktop session logged in.

## Notes & worklog

- 2026-08-21: Scoped. Estimate is half a day, and only about an hour of it is
  `serve.py` and the `pyproject.toml` entry. Authentication and the mount layout
  are the rest.

- **Authentication is the fiddly part.** The SDK spawns the CLI: its `_find_cli`
  checks for a bundled copy, then `shutil.which("claude")`, so the image needs
  Node and `@anthropic-ai/claude-code` installed. The subscription login lives in
  `~/.claude/.credentials.json` and its OAuth refresh writes back, so that mount
  has to be writable. Logging in inside a fresh container wants a browser and is
  worth avoiding; `ANTHROPIC_API_KEY` is the alternative if billing by API key is
  acceptable.

- **The image is a development environment, not an application.** `vcs.py` shells
  out to `git` for every project view, and beyond that the container needs
  whatever toolchain the code being worked on needs. That, rather than the Python
  packaging, is what decides the image size.

- **Notifications now require HTTPS.** `window.py:1028` sends a
  `Gio.Notification`, which goes away with the window. The browser path exists
  (`remote/static/app.js:687-721`) but `window.Notification` is undefined outside
  a secure context, so the TLS that [[ISSUE-022]] set up stops being optional.

- **Settings become file edits.** `settings_view.py` is the only way to change
  model, effort, thinking visibility and to rotate the token. [[ISSUE-030]] kept
  token and bind out of the browser on the grounds that a phone flipping the bind
  would open this machine's port to the LAN. In a container the bind argument
  goes away, since the network namespace is the boundary, so exposing the safe
  subset of settings in the browser is the natural follow-up. Out of scope here.

- **Also lost:** terminal-kind sessions, which are VTE and were already secondary
  to SDK sessions, and the QR code from [[ISSUE-029]], which lived on the
  Settings tab. `qr.py` is pure Python, so a route could render it later; for now
  the startup line prints the URL.

## Resolution
