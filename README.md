# Codinian

Codinian is a GTK4/libadwaita desktop app for Linux that runs several Claude
Code sessions at once and shows each one as a live, readable transcript. Every
session is bound to its own working directory. Alongside the sessions it keeps a
per-project workspace, browsing files, editing an in-repo issue tracker, and
running the common git operations. The same sessions can be reached from a phone
or another laptop over your own network or a Tailscale tailnet.

It is built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python),
so it drives your existing local Claude Code installation rather than talking to
the Anthropic API directly.

> **Status: 0.1.** Linux-only, no packaged install yet, and no automated test
> suite. It runs and is used daily, but expect rough edges. Known gaps are
> tracked in [`issues/`](issues/); the open ones are worth reading before you
> rely on it.

<!-- Screenshots go here once captured on a clean desktop; see issues/ for the
     screenshot task. -->

## What it does

**Sessions as transcripts, not terminals.** A session driven through the Agent
SDK renders as a stream of typed events: assistant messages with their markdown
intact, thinking blocks, tool calls as cards you can fold open, inline diffs for
every Edit and Write, plan documents, and nested subagent work. Every tool call
that needs approval stops and waits for an Approve or Deny, from any connected
client. A second, older session kind runs the `claude` CLI in a raw VTE terminal
for cases the transcript view does not cover.

**A cross-session approval inbox.** When several sessions are working at once,
their pending approvals collect in one place, so a session that is blocked
waiting on you does not sit unnoticed behind another session's output.

**A project workspace.** Register a folder and it gets four tabs:

- **Files**: a lazy directory tree; view and edit a file, rename it, or open it
  in whatever application the desktop associates with its type.
- **Issues**: a form editor over a plain-Markdown issue tracker kept in the repo
  (the same `issues/` convention this project uses), with filters driven by the
  repo's own `.codinian/settings.json`.
- **Git**: status, stage-and-commit, tag, initialise a repo, and edit
  `.gitignore`.
- **Sessions**: the sessions running in this project now, plus every past
  `claude` conversation recorded under `~/.claude/projects/`, each one
  resumable.

**Remote and phone access.** Everything except the desktop Settings pane is a web
page served by a bundled server on `127.0.0.1:8787`. The desktop app shows those
pages in WebKitGTK panes; a browser or phone reaches the identical pages over the
network. A token guards every API call, and Settings shows a QR code for the link
a phone can actually use. The recommended remote path is `tailscale serve`, which
keeps the bind on loopback and adds TLS. See
[docs/remote-access.md](docs/remote-access.md) for the full model.

**Resume where a session left off.** Resuming reads the mode a conversation was
last running under from its own transcript, so a session that had been running
unattended in an auto-approve mode comes back in that mode rather than dropping to
"ask every time".

**The smaller things.** Per-session cost and token totals; the plan's usage
windows read out of a `/usage` you run yourself; permission-mode switching mid
session; full-text search across every stored transcript; session templates with
per-folder permission defaults; streaming output with an interrupt; desktop
notifications for approvals; and a light/dark theme applied to both the GTK shell
and the web panes.

## Requirements

- Linux with a working [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
  install: the `claude` CLI on `PATH`, already signed in.
- Python 3.12 or newer.
- PyGObject with the typelibs for GTK 4, libadwaita 1, WebKitGTK 6.0, VTE 3.91,
  and Pango 1.0. On Fedora these are `python3-gobject`, `gtk4`, `libadwaita`,
  `webkitgtk6.0`, and `vte291-gtk4`; other distributions ship equivalents under
  their own names.
- Python packages `aiohttp`, `claude-agent-sdk`, and `qrcode` (the last is
  optional; without it the remote tab still works but draws no QR code).

The GTK stack is deliberately installed from the distribution rather than from
PyPI, because PyGObject builds against the system libraries.

## Install and run

Clone the repo and run it in place:

```bash
git clone <your-fork-url> codinian
cd codinian
pip install --user aiohttp claude-agent-sdk qrcode   # if not already present
python3 main.py
```

To add a desktop launcher and icon for the current user, pointing at this
checkout:

```bash
packaging/install.sh
```

Run `packaging/install.sh --uninstall` to remove them. Nothing is copied except
the `.desktop` entry and the icon; the app still runs from the checkout.

## How it talks to Claude

Codinian starts and supervises your own local `claude` process through the
Claude Agent SDK. Authentication is whatever `claude` itself uses on your machine
(your Claude subscription login or an API key you have configured for Claude
Code). Codinian does not proxy, store, or forward Anthropic credentials, and it
adds no account of its own.

On a subscription the per-session dollar figure is not a bill, so the transcript
footer shows the plan's usage windows by default and leaves the cost total off;
an API-key user can turn the cost total back on in Settings.

## Security in one paragraph

The server binds to loopback by default. Approving a tool call over it runs that
tool on this machine, so an open port is a shell with a button; the token is the
only credential, and there is no TLS on a plain LAN bind. Use the Tailscale path
for anything beyond this machine, treat the link like an SSH key, and rotate the
token to revoke access. The full threat model, including the `tailscale serve`
identity headers and why they are attribution rather than authentication, is in
[docs/remote-access.md](docs/remote-access.md). Report a vulnerability per
[SECURITY.md](SECURITY.md).

## Documentation

- [docs/remote-access.md](docs/remote-access.md): the remote server, its API, and
  its security model.
- [docs/transcript-protocol.md](docs/transcript-protocol.md): the WebSocket event
  protocol between the server and its clients.
- [docs/project-workspace-protocol.md](docs/project-workspace-protocol.md): the
  project registry, the `/api/projects` routes, and the issue format.
- [issues/](issues/): the project's own tracker, one Markdown file per issue.

## License

GPL-3.0-only. See [LICENSE](LICENSE).

## Credits

The `no-ai-slop` skill under [`skills/`](skills/) is derived from Louis Rossmann's
[no_ai_slop_writing_rules](https://github.com/realrossmanngroup/no_ai_slop_writing_rules).
