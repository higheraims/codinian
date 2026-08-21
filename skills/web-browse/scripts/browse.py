#!/usr/bin/env python3
"""Fetch or search the web through headless Firefox, and print the page as text.

    browse.py fetch  URL
    browse.py search 'some query'
    browse.py fetch  URL --html          # DOM instead of rendered text
    browse.py fetch  URL --links         # every link on the page
    browse.py fetch  URL --wait 30       # seconds to let the page settle

Why this exists: bugs.winehq.org and gitlab.winehq.org sit behind Anubis, which
demands a JavaScript proof-of-work before serving anything. curl and plain HTTP
fetchers get a "Making sure you're not a bot!" stub. A real browser solves the
challenge and gets the page, so this drives one.

It talks to Firefox's built-in Marionette remote protocol over a socket, so it
needs no geckodriver, no selenium and no pip install. It runs headless against a
throwaway profile, so it never touches the profile you browse with, and it will
not disturb a Firefox you already have open.

Each run picks its own free Marionette port (or honors BROWSE_MARIONETTE_PORT
if set) so that concurrent runs never fight over one fixed port.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

PROFILE_PREFS = """
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.startup.page", 0);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("extensions.autoDisableScopes", 15);
user_pref("marionette.port", %d);
"""


def pick_port() -> int:
    """Reserve a Marionette port for this run.

    BROWSE_MARIONETTE_PORT wins if set, for callers that want an explicit
    port. Otherwise, bind to port 0 to get an ephemeral port the OS
    guarantees is currently free, then release it so Firefox can bind it a
    moment later. This is the fix for the original bug: with a single fixed
    default port, two concurrent runs raced for it, the loser's Firefox
    failed to bind, and the loser's launch() loop then found something
    answering on that port anyway -- the winner's browser -- and quietly
    drove it instead of its own. Handing each run its own OS-assigned port
    removes the collision rather than papering over it.
    """
    override = os.environ.get("BROWSE_MARIONETTE_PORT")
    if override:
        return int(override)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Marionette:
    """Just enough of the Marionette wire protocol: length-prefixed JSON arrays."""

    def __init__(self, port: int, timeout: float = 90.0):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""
        self.msgid = 0
        self._read_packet()  # server handshake

    def _read_packet(self) -> object:
        while b":" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("Firefox closed the Marionette connection")
            self.buf += chunk
        length, _, rest = self.buf.partition(b":")
        need = int(length)
        while len(rest) < need:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("Firefox closed the Marionette connection")
            rest += chunk
        self.buf = rest[need:]
        return json.loads(rest[:need])

    def call(self, command: str, params: dict | None = None) -> object:
        self.msgid += 1
        payload = json.dumps([0, self.msgid, command, params or {}]).encode()
        self.sock.sendall(b"%d:%s" % (len(payload), payload))
        while True:
            msg = self._read_packet()
            if isinstance(msg, list) and len(msg) == 4 and msg[0] == 1 and msg[1] == self.msgid:
                _, _, error, result = msg
                if error:
                    raise RuntimeError(f"{command}: {error.get('message', error)}")
                # Marionette wraps every reply in {"value": ...}.
                if isinstance(result, dict) and set(result) == {"value"}:
                    return result["value"]
                return result

    def close(self) -> None:
        try:
            self.call("Marionette:Quit")
        except Exception:
            pass
        self.sock.close()


def launch(profile: Path, port: int) -> subprocess.Popen:
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "user.js").write_text(PROFILE_PREFS % port)
    proc = subprocess.Popen(
        ["firefox", "--headless", "--marionette", "--no-remote",
         "--profile", str(profile), "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "MOZ_MARIONETTE_PORT": str(port)},
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Firefox exited with {proc.returncode} before Marionette came up")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        except OSError:
            time.sleep(0.4)
            continue
        # Something answers on our port. Confirm it is still our own child
        # before trusting it -- if Firefox died in the instant between the
        # connect above and here, whatever answered is not the browser we
        # asked for, and attaching to it would be exactly the silent
        # wrong-browser bug this function exists to prevent.
        if proc.poll() is not None:
            raise RuntimeError(
                f"Firefox (pid {proc.pid}) exited with {proc.returncode} just after "
                f"port {port} opened; refusing to attach to whatever is listening there"
            )
        return proc
    proc.kill()
    raise RuntimeError(f"Firefox did not open Marionette port {port} within 60s")


# Pages worth waiting on go through several documents before the real one:
# Anubis serves a challenge, then a "Success!" interstitial, then redirects, and
# Bugzilla then fills its own results in with script. Each of those unloads the
# document, which kills any script running inside it, so poll from out here.
INTERSTITIAL = (
    "not a bot", "making sure you", "verifying", "just a moment",
    "checking your browser", "protected by anubis", "please stand by",
    "data is being retrieved", "loading",
)


def settle(client: "Marionette", seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            text = client.call("WebDriver:ExecuteScript",
                               {"script": "return document.readyState + '\\x00' + "
                                          "(document.body ? document.body.innerText : '');",
                                "args": []}) or ""
        except RuntimeError:
            time.sleep(0.5)      # document unloaded mid-poll; it is navigating
            continue
        ready, _, body = text.partition("\x00")
        stripped = body.strip()
        low = stripped.lower()
        waiting = (ready != "complete"
                   or len(stripped) < 40
                   or any(marker in low for marker in INTERSTITIAL))
        if not waiting:
            return
        time.sleep(0.5)


def render(url: str, wait: float, mode: str) -> str:
    profile = Path(tempfile.mkdtemp(prefix="browse-profile-"))
    proc = None
    client = None
    try:
        port = pick_port()
        proc = launch(profile, port)
        client = Marionette(port)
        client.call("WebDriver:NewSession", {"capabilities": {}})
        client.call("WebDriver:SetTimeouts", {"pageLoad": 60000, "script": 30000})
        try:
            client.call("WebDriver:Navigate", {"url": url})
        except RuntimeError as exc:
            # A challenge page that redirects mid-load reports a navigation error
            # even though the browser is fine; the settle poll below sorts it out.
            print(f"# navigate: {exc}", file=sys.stderr)
        settle(client, wait)

        if mode == "html":
            return client.call("WebDriver:GetPageSource")
        if mode == "links":
            script = ("return Array.from(document.querySelectorAll('a[href]'))"
                      ".map(a => a.innerText.trim().replace(/\\s+/g,' ') + ' -> ' + a.href)"
                      ".filter(s => s.length > 5).join('\\n');")
            return client.call("WebDriver:ExecuteScript", {"script": script, "args": []})
        return client.call("WebDriver:ExecuteScript",
                           {"script": "return document.body.innerText;", "args": []})
    finally:
        if client:
            client.close()
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="render one URL")
    f.add_argument("url")

    s = sub.add_parser("search", help="run a search and render the results page")
    s.add_argument("query", nargs="+")
    s.add_argument("--engine", default="duckduckgo",
                   choices=["duckduckgo", "google", "bing"])

    for p in (f, s):
        p.add_argument("--html", action="store_true", help="dump the DOM")
        p.add_argument("--links", action="store_true", help="list links instead of text")
        p.add_argument("--wait", type=float, default=25.0,
                       help="seconds to wait for the page to settle (default 25)")

    args = ap.parse_args()

    if args.cmd == "search":
        q = urllib.parse.quote_plus(" ".join(args.query))
        url = {"duckduckgo": f"https://duckduckgo.com/?q={q}",
               "google": f"https://www.google.com/search?q={q}",
               "bing": f"https://www.bing.com/search?q={q}"}[args.engine]
        if not args.html and not args.links:
            args.links = True   # a results page is only useful as its links
    else:
        url = args.url

    mode = "html" if args.html else "links" if args.links else "text"
    if not shutil.which("firefox"):
        print("firefox is not on PATH", file=sys.stderr)
        return 1
    try:
        print(render(url, args.wait, mode))
    except Exception as exc:
        print(f"browse.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
