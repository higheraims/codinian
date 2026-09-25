"""A real GTK window, driven headlessly, reporting what reached it as JSON.

Run as a child process, never imported by the suite. The reason is measured
rather than assumed: `from gi.repository import Gtk` opens a display at import
time, so `GDK_BACKEND` has to be set before any import in the process that
opens the window. pytest imports every test module during collection, and
`tests/test_window.py` imports `codinian.window`, which imports Gtk. In one
process the window would therefore open on whatever display the developer
happens to be logged into, and in CI it would fail in a different way. A child
gets the environment set before its first import, and takes the GTK and WebKit
processes out of the test runner with it (ISSUE-067).

Two programs and no display:

- `gtk4-broadwayd :N` is a display server that renders to a web page instead of
  a screen. Nothing to install and nothing needing root, unlike Xvfb.
- headless Chromium loads that page, and `Input.dispatchMouseEvent` over the
  DevTools protocol puts real input through broadway into the widget. aiohttp
  is already a dependency and speaks the WebSocket.

What this cannot do is synthesise `TOUCHPAD_PINCH`, so the pinch gesture itself
stays a hardware check. What it can do is the half that was actually broken:
deliver events to the controller and see whether the handler survives them.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request

BROADWAY_BASE_PORT = 8080
MOUSE_MOVES = 30
SETTLE_SECONDS = 2.0
DEADLINE_SECONDS = 60.0


def wait_for_port(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return True
        except OSError:
            time.sleep(0.05)
    return False


def free_display() -> tuple[int, int]:
    """A broadway display number whose port nothing is listening on. Broadway
    serves display :N on port 8080+N, so the port is the thing to check."""
    for n in range(20, 80):
        port = BROADWAY_BASE_PORT + n
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return n, port
    raise RuntimeError("no free broadway display")


def drive_input(port: int, cdp_port: int, moves: int, done: threading.Event) -> None:
    """Move a real pointer across the broadway page. Runs on its own thread
    with its own event loop, because the GTK main context has to keep being
    pumped on the main thread while this happens."""
    import aiohttp

    async def go() -> None:
        # Its own profile directory, thrown away after. Without this the
        # browser writes into whatever HOME it inherited, which is the
        # developer's real Chromium profile when this file is run by hand.
        #
        # Removed at exit as well as below: Chromium's renderer and zygote
        # processes outlive the terminate on the parent by a moment, and a
        # rmtree that lands in that moment leaves a directory behind with no
        # complaint, because it is called with ignore_errors.
        profile = tempfile.mkdtemp(prefix="codinian-probe-chromium-")
        atexit.register(shutil.rmtree, profile, ignore_errors=True)
        browser = subprocess.Popen(
            ["chromium-browser", "--headless", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={profile}",
             f"--remote-debugging-port={cdp_port}", "--remote-allow-origins=*",
             f"http://127.0.0.1:{port}/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            page = None
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    tabs = json.load(urllib.request.urlopen(
                        f"http://127.0.0.1:{cdp_port}/json", timeout=2))
                    page = next(t for t in tabs if t["type"] == "page"
                                and t.get("webSocketDebuggerUrl"))
                    break
                except Exception:
                    await asyncio.sleep(0.25)
            if page is None:
                return
            # The page has to finish connecting to broadway and drawing the
            # window before a pointer over it lands on a widget.
            await asyncio.sleep(SETTLE_SECONDS)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(page["webSocketDebuggerUrl"]) as ws:
                    for i in range(moves):
                        await ws.send_json({
                            "id": i + 1,
                            "method": "Input.dispatchMouseEvent",
                            "params": {"type": "mouseMoved", "button": "none",
                                       "clickCount": 0,
                                       "x": 120 + i * 4, "y": 120 + (i % 5)},
                        })
                        await asyncio.sleep(0.04)
                    await asyncio.sleep(0.5)
        finally:
            browser.terminate()
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser.kill()
            shutil.rmtree(profile, ignore_errors=True)

    try:
        asyncio.run(go())
    finally:
        done.set()


def rgba_hex(colour) -> str:
    """A `Gdk.RGBA` as `#rrggbb`, with the alpha appended when it is not opaque,
    so a colour can be compared against the stylesheet's own notation."""
    channels = (colour.red, colour.green, colour.blue)
    hexed = "#" + "".join(f"{round(v * 255):02x}" for v in channels)
    return hexed if colour.alpha == 1.0 else f"{hexed}@{colour.alpha:g}"


def main() -> int:
    display, port = free_display()
    broadway = subprocess.Popen(["gtk4-broadwayd", f":{display}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_for_port(port):
        broadway.terminate()
        print(json.dumps({"error": "broadway never listened"}))
        return 1

    # Before the first gi import, which is the whole reason this is a child.
    os.environ["GDK_BACKEND"] = "broadway"
    os.environ["BROADWAY_DISPLAY"] = f":{display}"

    try:
        import gi
        gi.require_version("Adw", "1")
        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Adw, GLib, Gtk, WebKit

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from codinian.window import CodinianWindow

        # PyGObject prints an exception raised inside a signal handler and
        # carries on, so a handler that raises on every event is invisible
        # unless something records it. It goes through sys.excepthook, which is
        # what turns ISSUE-062 from 175,686 lines in the journal into a number
        # a test can assert on.
        handler_errors: list[str] = []

        def record(exc_type, exc, tb):
            handler_errors.append("".join(
                traceback.format_exception(exc_type, exc, tb)))

        sys.excepthook = record

        window = Gtk.Window(title="codinian probe", default_width=600,
                            default_height=400)
        webview = WebKit.WebView()
        # What the view would paint if nobody told it otherwise, recorded
        # before the call that tells it (ISSUE-079).
        default_bg = rgba_hex(webview.get_background_color())
        window.set_child(webview)

        # The dark palette is the one the flash was worth fixing for, so force
        # it and ask the real method what it puts on the view.
        Adw.init()
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        CodinianWindow._apply_pane_background(None, webview)
        pane_bg = rgba_hex(webview.get_background_color())

        # The real controller from the real module, installed the way
        # _build_webview_pane installs it.
        CodinianWindow._redirect_pinch_to_zoom(None, webview)

        # A second controller behind it, only to count what arrived and what
        # the signal carried. The one under test swallows nothing but pinches,
        # so this sees the same events.
        seen = {"events": 0, "signal_arg_none": 0, "current_event_typed": 0,
                "types": set()}

        def observe(controller, event):
            seen["events"] += 1
            if event is None:
                seen["signal_arg_none"] += 1
            current = controller.get_current_event()
            if current is not None:
                seen["current_event_typed"] += 1
                seen["types"].add(current.get_event_type().value_nick)
            return False

        observer = Gtk.EventControllerLegacy()
        observer.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        observer.connect("event", observe)
        webview.add_controller(observer)

        window.present()

        done = threading.Event()
        threading.Thread(target=drive_input,
                         args=(port, port + 1200, MOUSE_MOVES, done),
                         daemon=True).start()

        context = GLib.MainContext.default()
        started = time.time()
        while time.time() - started < DEADLINE_SECONDS and not done.is_set():
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)
        # Anything the last events queued still has to run.
        deadline = time.time() + 1.0
        while time.time() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)

        sys.excepthook = sys.__excepthook__
        print(json.dumps({
            "realized": window.get_realized(),
            "backend": type(window.get_display()).__name__,
            "zoom_level": webview.get_zoom_level(),
            "default_pane_bg": default_bg,
            "dark_pane_bg": pane_bg,
            "moves_sent": MOUSE_MOVES,
            "events": seen["events"],
            "signal_arg_none": seen["signal_arg_none"],
            "current_event_typed": seen["current_event_typed"],
            "types": sorted(seen["types"]),
            "handler_errors": handler_errors,
        }))
        return 0
    finally:
        broadway.terminate()
        try:
            broadway.wait(timeout=5)
        except subprocess.TimeoutExpired:
            broadway.kill()


if __name__ == "__main__":
    sys.exit(main())
