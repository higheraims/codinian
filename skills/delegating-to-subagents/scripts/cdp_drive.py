#!/usr/bin/env python3
"""Drive a real page in headless Chromium over the DevTools Protocol.

For verifying browser UI without a human clicking, and without trusting a mock
to behave like the server. Evaluates JavaScript in the page, clicks things, and
captures screenshots.

    cdp_drive.py URL --eval "document.title"
    cdp_drive.py URL --click ".btn-approve" --wait 3 --eval "document.body.innerText"
    cdp_drive.py URL --shot out.png --dark
    cdp_drive.py URL --capture-ws --click ".btn-approve"

--capture-ws wraps WebSocket.prototype.send before the click and prints every
frame the page sent, which is how you check what the client actually transmits
rather than what it was supposed to.

Needs aiohttp and a chromium binary. Give concurrent runs different --port and
--profile values so they do not share one browser profile.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile

import aiohttp

CHROMIUM_CANDIDATES = ("chromium-browser", "chromium", "google-chrome", "chrome")


def find_chromium() -> str | None:
    for name in CHROMIUM_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


class Page:
    """One attached page target. `evaluate` returns the JS value."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse):
        self._ws = ws
        self._id = 0

    async def _call(self, method: str, params: dict | None = None):
        self._id += 1
        call_id = self._id
        await self._ws.send_json({"id": call_id, "method": method,
                                  "params": params or {}})
        async for message in self._ws:
            data = json.loads(message.data)
            if data.get("id") == call_id:
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result", {})
        raise RuntimeError(f"socket closed waiting for {method}")

    async def evaluate(self, expression: str):
        result = await self._call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        details = result.get("exceptionDetails")
        if details:
            # The bare "text" is usually just "Uncaught", which says nothing.
            # The exception's own description names what actually failed.
            exception = details.get("exception") or {}
            message = (exception.get("description") or exception.get("value")
                       or details.get("text") or "evaluation failed")
            raise RuntimeError(str(message).splitlines()[0])
        return result.get("result", {}).get("value")

    async def screenshot(self, path: str) -> None:
        result = await self._call("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(result["data"]))

    async def emulate_color_scheme(self, scheme: str) -> None:
        await self._call("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-color-scheme", "value": scheme}],
        })


async def attach(session: aiohttp.ClientSession, port: int, url_fragment: str,
                 timeout: float = 20.0) -> str:
    """The debugger URL of the page target serving `url_fragment`."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.4)
        try:
            async with session.get(f"http://127.0.0.1:{port}/json") as response:
                for target in await response.json():
                    if target.get("type") == "page" and url_fragment in target.get("url", ""):
                        return target["webSocketDebuggerUrl"]
        except aiohttp.ClientError:
            continue
    raise RuntimeError("could not attach to a page target; is the URL loading?")


# Wraps WebSocket.prototype.send, runs the caller's snippet, and reports every
# frame sent in between. The point is to see what the page transmits, which is
# where client bugs hide that a hand-written test payload cannot reach.
CAPTURE_TEMPLATE = """(() => {
  const sent = [];
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { sent.push(String(d)); return orig.call(this, d); };
  try { %s } finally { WebSocket.prototype.send = orig; }
  return sent;
})()"""


async def run(args) -> int:
    binary = find_chromium()
    if binary is None:
        print(f"no chromium binary found (tried {', '.join(CHROMIUM_CANDIDATES)})",
              file=sys.stderr)
        return 2

    profile = args.profile or tempfile.mkdtemp(prefix="cdp-profile-")
    process = subprocess.Popen(
        [binary, "--headless=new", f"--remote-debugging-port={args.port}",
         "--no-first-run", "--no-default-browser-check",
         f"--window-size={args.window}", f"--user-data-dir={profile}", args.url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        async with aiohttp.ClientSession() as session:
            debugger_url = await attach(session, args.port, args.url_fragment or "")
            async with session.ws_connect(debugger_url, max_msg_size=0) as ws:
                page = Page(ws)
                if args.dark:
                    await page.emulate_color_scheme("dark")
                await asyncio.sleep(args.settle)

                # Steps run in the order they were written, so a --eval that
                # sets something up can precede the --click that needs it.
                for kind, value in args.steps or []:
                    if kind == "eval":
                        print(json.dumps(await page.evaluate(value), default=str))
                        continue
                    selector = json.dumps(value)
                    present = await page.evaluate(f"!!document.querySelector({selector})")
                    if not present:
                        print(f"no element matched {value}", file=sys.stderr)
                        return 1
                    snippet = f"document.querySelector({selector}).click();"
                    if args.capture_ws:
                        frames = await page.evaluate(CAPTURE_TEMPLATE % snippet)
                        for frame in frames or []:
                            print(f"sent: {frame}")
                    else:
                        await page.evaluate(snippet)
                        print(f"clicked: {value}")
                    await asyncio.sleep(args.wait)

                if args.shot:
                    await page.screenshot(args.shot)
                    print(f"screenshot: {args.shot}")
        return 0
    finally:
        process.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    class Step(argparse.Action):
        """Collects --eval and --click into one list, preserving their order."""

        def __call__(self, parser, namespace, values, option_string=None):
            steps = getattr(namespace, "steps", None) or []
            steps.append((self.dest, values))
            namespace.steps = steps

    parser.add_argument("url")
    parser.add_argument("--eval", action=Step, metavar="JS", dest="eval",
                        help="evaluate an expression and print it; repeatable, ordered")
    parser.add_argument("--click", action=Step, metavar="SELECTOR", dest="click",
                        help="click one element; repeatable, ordered with --eval")
    parser.add_argument("--capture-ws", action="store_true",
                        help="print WebSocket frames the page sends during the click")
    parser.add_argument("--shot", metavar="PATH", help="write a screenshot")
    parser.add_argument("--dark", action="store_true", help="emulate a dark colour scheme")
    parser.add_argument("--settle", type=float, default=3.0,
                        help="seconds to let the page load before acting (default 3)")
    parser.add_argument("--wait", type=float, default=2.0,
                        help="seconds to wait after clicking (default 2)")
    parser.add_argument("--port", type=int, default=9222,
                        help="devtools port; give concurrent runs different ports")
    parser.add_argument("--profile", help="chromium user-data-dir; defaults to a temp dir")
    parser.add_argument("--window", default="1200,900")
    parser.add_argument("--url-fragment",
                        help="substring identifying the page target (defaults to the URL)")
    args = parser.parse_args()
    if not hasattr(args, "steps"):
        args.steps = []
    if args.url_fragment is None:
        args.url_fragment = args.url.split("//", 1)[-1].split("/", 1)[0]
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
