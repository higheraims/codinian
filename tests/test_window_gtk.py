"""A real window, a real display, real events, and no screen.

The other 620 tests cover everything except the part with the display in it,
which is how ISSUE-062 shipped: `_redirect_pinch_to_zoom` raised AttributeError
on its first line for every event delivered to a pane, logged 175,686
tracebacks in one 13 hour run, and was found by reading the journal rather than
by anything failing.

What runs here is the genuine `CodinianWindow._redirect_pinch_to_zoom`,
installed on a genuine `WebKit.WebView`, inside a genuine
`Gtk.ApplicationWindow`, with a pointer moved across it by a browser. The setup
and the reason it is a child process are in `tests/gtk_probe.py`.

It costs about ten seconds and needs `gtk4-broadwayd` and `chromium-browser`,
so it skips rather than fails where either is missing, and carries the `gtk`
mark for `-m 'not gtk'`.

**What it does not catch.** A handler rewritten to read the signal argument and
guard it with `if event is None: return False` raises nothing and does nothing:
pinch-to-zoom would be dead and every count here would still be clean. That
failure was reachable only by reintroducing it by hand, which is what
`test_the_signal_argument_is_never_the_event` exists to warn the next reader
about. The gesture itself stays a hardware check either way, because broadway
cannot synthesise `TOUCHPAD_PINCH`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gtk

PROBE = Path(__file__).parent / "gtk_probe.py"
MISSING = [name for name in ("gtk4-broadwayd", "chromium-browser")
           if shutil.which(name) is None]

pytest.importorskip("aiohttp", reason="the probe drives Chromium over a WebSocket")

if MISSING:
    pytest.skip(f"needs {' and '.join(MISSING)}", allow_module_level=True)


@pytest.fixture(scope="session")
def probe() -> dict:
    """One window, one browser, one run. Session-scoped because starting them
    is what costs the ten seconds; the assertions below are free."""
    # conftest.py repoints HOME at a sandbox, which moves the user
    # site-packages directory and hides aiohttp from anything started
    # afterwards. This interpreter resolved its own sys.path before that
    # happened, so handing it over gives the child the imports the parent has
    # without letting it out of the sandbox.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    result = subprocess.run([sys.executable, str(PROBE)], capture_output=True,
                            text=True, timeout=180, env=env)
    if result.returncode != 0 or not result.stdout.strip():
        pytest.fail("the probe did not report:\n"
                    f"exit {result.returncode}\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


def test_a_window_opens_with_no_display(probe):
    # Not the developer's desktop, which is what GDK would pick up if anything
    # imported Gtk before the backend was chosen.
    assert probe["realized"] is True
    assert probe["backend"] == "GdkBroadwayDisplay"


def test_events_reach_a_pane_at_all(probe):
    # Every assertion below is about what happened to the events. If none
    # arrived they would all pass by vacancy.
    assert probe["events"] == probe["moves_sent"]
    assert probe["types"] == ["motion-notify"]


def test_no_handler_on_a_pane_raises(probe):
    # ISSUE-062, as the acceptance criterion words it: a handler that raises on
    # every event fails the suite rather than filling the journal. PyGObject
    # prints and carries on, so the probe records sys.excepthook instead.
    #
    # Checked by putting the bug back: with `event = _event` and no None guard,
    # this run reports 30 of these, each
    # "AttributeError: 'NoneType' object has no attribute 'get_event_type'".
    assert probe["handler_errors"] == []


def test_the_signal_argument_is_never_the_event(probe):
    # `GtkEventControllerLegacy::event` declares a GdkEvent*, which is a
    # fundamental type rather than a GObject, and PyGObject 3.56 hands the
    # handler None in its place. This is the fact the pinch controller is
    # written around, and the reason a handler must not read its argument.
    assert probe["signal_arg_none"] == probe["events"]


def test_the_controller_can_be_asked_for_the_event_instead(probe):
    # The other half of the ISSUE-062 diagnosis. get_current_event() returns a
    # typed event for every event the signal delivered as None, which is what
    # makes the workaround a workaround rather than a guess.
    assert probe["current_event_typed"] == probe["events"]


def test_a_pane_is_left_at_its_own_zoom_level(probe):
    # Nothing but a pinch may move it, and no pinch was sent. A controller that
    # acted on motion events would show up here.
    assert probe["zoom_level"] == 1.0
