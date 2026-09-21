# Codinian

<img src="docs/art/codinian.svg" align=right size=100x100>Codinian is a GTK4/libadwaita desktop app for Linux that runs several Claude
Code sessions at once and shows each one as a live, readable transcript. Every
session is bound to its own working directory. Alongside the sessions it keeps a
per-project workspace, browsing files, editing an in-repo issue tracker, and
running the common git operations. The same sessions can be reached from a phone
or another laptop over your own network or a Tailscale tailnet.

It is built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python),
so it drives your existing local Claude Code installation rather than talking to
the Anthropic API directly.

> **Status: alpha.** Linux-only, no packaged install yet, and no automated test
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
client, and a question the model asks outright arrives as its options on
buttons rather than as a prompt you have to read and retype. The composer stays
live while a turn runs, so a word to redirect Claude reaches it between tool
calls without ending the turn. A second, older session kind runs the `claude`
CLI in a raw VTE terminal for cases the transcript view does not cover.

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
per-folder permission defaults; streaming output with a Stop that also clears
what was queued behind the turn; desktop notifications for approvals; and a
light/dark theme applied to both the GTK shell and the web panes.

## Requirements

- Linux with a working [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
  install: the `claude` CLI on `PATH`, already signed in.
- Python 3.12 or newer.
- PyGObject with the typelibs for GTK 4, libadwaita 1, WebKitGTK 6.0, VTE 3.91,
  and Pango 1.0. On Fedora these are `python3-gobject`, `gtk4`, `libadwaita`,
  `webkitgtk6.0`, and `vte291-gtk4`; other distributions ship equivalents under
  their own names.
- Python packages `aiohttp` 3.12.14 or newer, `claude-agent-sdk`, and `qrcode`
  (the last is optional; without it the remote tab still works but draws no QR
  code). The aiohttp floor is a security one: it carries the server, and
  3.12.14 fixed the last request-smuggling hole in its pure-Python HTTP parser.

The GTK stack is deliberately installed from the distribution rather than from
PyPI, because PyGObject builds against the system libraries.

## Install and run

Clone the repo and run it in place:

```bash
git clone <your-fork-url> codinian
cd codinian
pip install --user 'aiohttp>=3.12.14' pyyaml qrcode                 # if not already present
pip install --user --no-binary claude-agent-sdk claude-agent-sdk    # see the note below
python3 -m codinian
```

`--no-binary` for the SDK alone: the wheel on PyPI is 317 MB because it bundles
its own copy of the `claude` binary. Built from the source distribution instead
it is 345 KB, and it uses the `claude` already on your PATH.

If you install the full wheel anyway, that bundled copy is only a fallback.
Codinian runs the `claude` on this machine by default, because that is the one
your package manager keeps current, and the bundled one is frozen at whatever
version the SDK release was built around. Settings > Claude chooses between
them or takes a path of your own, and Settings > About says which is running
and what version it is.

To add a desktop launcher and icon for the current user, pointing at this
checkout:

```bash
packaging/install.sh
```

Run `packaging/install.sh --uninstall` to remove them. Nothing is copied except
the `.desktop` entry and the icon; the app still runs from the checkout.

RPM and Debian packaging, the Fedora COPR repository, and the one dependency no
released Debian or Ubuntu can currently satisfy are in
[docs/packaging.md](docs/packaging.md).

## Tests

```bash
pip install --user pytest    # if not already present
python3 -m pytest
```

620 tests, about four seconds, no network and no display. They cover the parts
of the app that do not import `gi`: the issue format, the project registry and
its settings, path containment, git, the config and session database, the event
bus, the permission-mode tables, transcript reading and search, and the
server's authentication middleware. The GTK layer (`codinian/window.py`,
`codinian/settings_view.py`, the dialogs) and the SDK turn loop have no tests.

Nothing is mocked where the real thing will do: `test_vcs.py` runs against
real repositories in a temp directory, and `test_server_auth.py` drives a real
aiohttp application. `tests/conftest.py` repoints `HOME` and git's config
before any app module is imported, so a run cannot reach your own session
database, project registry or transcripts; `test_sandbox.py` checks that
repointing still works.

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
- [docs/packaging.md](docs/packaging.md): building the rpm and deb, the COPR
  repository, and what each distribution can satisfy.
- [issues/](issues/): the project's own tracker, one Markdown file per issue.

## License

GPL-3.0-only. See [LICENSE](LICENSE).

## Credits

The `no-ai-slop` skill under [`skills/`](skills/) is derived from Louis Rossmann's
[no_ai_slop_writing_rules](https://github.com/realrossmanngroup/no_ai_slop_writing_rules).
