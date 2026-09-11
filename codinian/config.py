"""User configuration for the remote server (ISSUE-007).

Holds the access token and the bind address, in a JSON file next to the rest of
the user's config. The token gates every `/api/` call and the WebSocket, so it
is generated on first run and the file is written 0600.

The bind address is deliberately a config key rather than a command-line flag:
reaching the LAN is a choice the user makes once and keeps, and the desktop app
offers a switch for it. Approving a tool call over this socket runs code on this
machine, so the default stays loopback.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

# `CODINIAN_CONFIG` points the app at a different config file, which is the
# supported way to run a second instance beside the real one: a config of its
# own means a port of its own, and the server binds a single port (see the
# "one instance only" note in docs/HANDOFF.md). Test runs use it; ordinary
# runs never set it.
CONFIG_PATH = Path(os.environ.get("CODINIAN_CONFIG") or
                   Path.home() / ".config/codinian/config.json")

# Overridable for the same reason CODINIAN_CONFIG is: two instances sharing an
# application id means the second hands its command line to the first instead of
# starting. Defined here so main.py and the About tab quote the same value.
APP_ID = os.environ.get("CODINIAN_APP_ID", "net.higheraims.codinian")

LOOPBACK = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"

DEFAULTS = {
    "token": "",
    "bind": LOOPBACK,
    "port": 8787,
    # Accept a Tailscale identity header in place of the token (ISSUE-022).
    # Off by default and it should usually stay off: a proxied request and a
    # forged local one are indistinguishable at the socket, both arriving from
    # 127.0.0.1, so this trades a secret only the owner can read for a header
    # any local process can set. See docs/remote-access.md.
    "trust_tailscale_identity": False,
    # How the window comes back. Size and maximised state only, because a
    # Wayland client cannot place its own window: GTK4 has no set_position and
    # placement belongs to the compositor.
    #
    # Position is not permanently out of reach, and this is worth knowing
    # before anyone tries to add x and y here. Wayland has a session-management
    # protocol for exactly this, and the compositor restores the window rather
    # than the app positioning itself. On this machine (Plasma 6, GTK 4.22.4)
    # both halves exist and do not meet: KWin advertises
    # `xdg_session_manager_v1`, GTK still binds the pre-standardisation
    # `xx_session_manager_v1`, and a WAYLAND_DEBUG trace shows the global
    # offered and never bound. When GTK moves to the standard name this starts
    # working with no code here, since it is a toolkit-level feature.
    "window_width": 1280,
    "window_height": 800,
    "window_maximized": True,
    # Interface preferences (ISSUE-030). "system" leaves both the GTK shell and
    # the WebKitGTK panes following the desktop; see theme.py.
    "theme": "system",
    "notifications": True,
    # Fallback permission mode for a new session in a folder that is not a
    # registered project. A project's own .codinian/settings.json wins.
    "default_permission_mode": "default",
    # How Codinian talks to Claude Code (ISSUE-032). See agent_options.py for
    # what each value means and why "default" is a distinct value from any
    # particular setting.
    "model": "",
    "effort": "default",
    "thinking": "default",
    "system_prompt_preset": True,
    "system_prompt_append": "",
    # Whether a subagent's prose and reasoning reach the transcript; its tool
    # calls arrive either way (ISSUE-017).
    "forward_subagent_text": True,
    # Whether assistant text appears as it is written (ISSUE-033). Text only;
    # thinking and tool arguments are not streamed.
    "stream_partial_text": True,
}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def load() -> dict:
    """Read the config, filling in defaults and generating a token on first
    run. Always returns a usable dict; a corrupt file is replaced rather than
    left to break startup."""
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError):
            data = {}

    config = {**DEFAULTS, **data}
    if not config.get("token"):
        config["token"] = new_token()
        save(config)
    return config


def save(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Written through a temp file so a crash mid-write cannot leave a
    # half-written config that locks the user out of their own server.
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_PATH)


def rotate_token(config: dict) -> str:
    """Replace the token and persist it. Existing clients are cut off at their
    next request, which is the point."""
    config["token"] = new_token()
    save(config)
    return config["token"]


def url_for(config: dict, host: str | None = None) -> str:
    """The URL to hand to a browser, token included."""
    host = host or (LOOPBACK if config["bind"] == LOOPBACK else local_ip())
    return f"http://{host}:{config['port']}/?token={config['token']}"


def local_ip() -> str:
    """This machine's LAN address, for the URL shown when the bind is open.
    Uses a UDP socket to pick the interface that would route out; no packet is
    sent. Falls back to loopback when there is no route."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere
        return sock.getsockname()[0]
    except OSError:
        return LOOPBACK
    finally:
        sock.close()
