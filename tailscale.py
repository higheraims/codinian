"""Tailscale integration for the remote path (ISSUE-022).

The intended way to reach Codinian from a phone is the tailnet: `tailscale
serve` terminates TLS at this machine's MagicDNS name and proxies to the app on
loopback, so the bind stays closed, the certificate is handled for us, and the
traffic is encrypted.

This module answers two questions for the rest of the app: what URL should the
Remote Access dialog show, and who is the tailnet user behind a request.

## What a proxied request actually looks like

Measured, not assumed. A request arriving through `tailscale serve` carries:

    Tailscale-User-Login: someone@github
    Tailscale-User-Name: someone
    Tailscale-User-Profile-Pic: https://...
    Tailscale-Headers-Info: https://tailscale.com/s/serve-headers
    X-Forwarded-For: 100.73.79.35
    X-Forwarded-Proto: https
    Host: <machine>.<tailnet>.ts.net

and its source address is **127.0.0.1**, because tailscaled connects to the
local port like any other local client.

That last part is the whole problem. A direct request from any local process
also arrives from 127.0.0.1, and can set every one of those headers itself. The
two are indistinguishable at the socket. So identity headers are evidence of who
Tailscale says is calling, and they are not proof that Tailscale is calling.

Consequently this module never treats identity as authentication on its own.
The token in config.py stays the credential. Identity is used to record who
approved a tool call, and it can be promoted to a credential only by the
explicit `trust_tailscale_identity` setting, which is off by default and
documented in docs/remote-access.md.
"""

from __future__ import annotations

import json
import subprocess
import time

CACHE_SECONDS = 15.0
_COMMAND_TIMEOUT = 4.0

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, producer):
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = producer()
    _cache[key] = (now, value)
    return value


def _run_json(args: list[str]):
    """Run a tailscale command and parse its JSON, or return None. Every
    failure mode (no binary, not running, timeout, unparseable) is the same
    answer here: we cannot say anything about Tailscale."""
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=_COMMAND_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        return None


def status() -> dict:
    """This node's Tailscale state: whether it is running, and its MagicDNS
    name with the trailing dot stripped."""

    def produce() -> dict:
        data = _run_json(["tailscale", "status", "--json"])
        if not isinstance(data, dict):
            return {"running": False, "dns_name": None, "tailnet": None}
        self_node = data.get("Self") or {}
        dns_name = (self_node.get("DNSName") or "").rstrip(".") or None
        return {
            "running": data.get("BackendState") == "Running",
            "dns_name": dns_name,
            "tailnet": data.get("MagicDNSSuffix"),
        }

    return _cached("status", produce)


def serve_target(port: int) -> dict | None:
    """The tailnet URL serving `port`, if `tailscale serve` is proxying it.

    Returns {"url": "https://host[:port]", "host": "host", "path": "/"} or None.
    """

    def produce():
        data = _run_json(["tailscale", "serve", "status", "--json"])
        if not isinstance(data, dict):
            return None
        proxy_target = f"http://127.0.0.1:{port}"
        for host_port, entry in (data.get("Web") or {}).items():
            for path, handler in ((entry or {}).get("Handlers") or {}).items():
                if (handler or {}).get("Proxy", "").rstrip("/") != proxy_target:
                    continue
                # host_port is "name:443" and 443 is implied in a URL.
                host, _, listen_port = host_port.rpartition(":")
                host = host or host_port
                url = f"https://{host}" if listen_port == "443" else f"https://{host}:{listen_port}"
                if path and path != "/":
                    url = url.rstrip("/") + path
                return {"url": url, "host": host, "path": path or "/"}
        return None

    return _cached(f"serve:{port}", produce)


def identity(headers) -> dict | None:
    """The tailnet user a request claims to be, or None.

    Requires the marker headers `tailscale serve` adds, so a request that
    merely sets a login header is not mistaken for a proxied one. This is a
    consistency check, not a proof: see the module docstring.
    """
    login = headers.get("Tailscale-User-Login")
    if not login:
        return None
    proxied = ("Tailscale-Headers-Info" in headers
               or headers.get("X-Forwarded-Proto") == "https")
    if not proxied:
        return None
    return {
        "login": login,
        "name": headers.get("Tailscale-User-Name") or login,
        "profile_pic": headers.get("Tailscale-User-Profile-Pic"),
    }
