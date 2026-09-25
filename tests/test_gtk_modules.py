"""The logic inside the other modules that import `gi`.

ISSUE-067 counted seven modules with no test file. `window.py` has two of its
own; this covers the decisions in the remaining six that do not need a widget
to reach. Importing them opens no display, which the first test here is there
to keep true: a module that grew a `Gtk.Window()` at import time would take the
whole suite off a headless machine.

What is deliberately not here is anything that builds a dialog or a preferences
page. Those are layout, and a test that asserts a row exists says only that
somebody wrote the line that adds it.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from codinian import main as main_module
from codinian import resume_dialog, settings_view, theme

GI_MODULES = ["main", "remote_panel", "resume_dialog", "settings_view",
              "session_dialog", "theme", "window"]


def test_every_gi_module_imports_with_no_display_to_open():
    """The suite has to run on a machine with no screen.

    Measured in a child with nothing to connect to, which is the only way to
    ask the question. `from gi.repository import Gtk` runs `Gtk.init()`, so on
    a desktop the import opens the developer's display whether or not it needs
    one, and `Gdk.Display.get_default()` in this process comes back with a
    GdkWaylandDisplay. Stripping the three variables that point at a display
    server leaves the imports with nothing to succeed by accident.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    names = ", ".join(GI_MODULES)
    result = subprocess.run(
        [sys.executable, "-c",
         f"from codinian import {names}\n"
         "from gi.repository import Gdk\n"
         "assert Gdk.Display.get_default() is None, 'a display was opened'\n"
         "print('ok')"],
        capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.parametrize("name", GI_MODULES)
def test_every_gi_module_is_importable(name):
    assert importlib.import_module(f"codinian.{name}") is not None


# -------------------------------------------------------------- the theme

@pytest.mark.parametrize("value", theme.THEMES)
def test_a_known_theme_is_kept(value):
    assert theme.normalize(value) == value


@pytest.mark.parametrize("value", [None, "", "System", "solarized", 3, [], {"a": 1}])
def test_anything_else_falls_back_to_following_the_desktop(value):
    # Config files get hand-edited. An unrecognised value must not reach a URL
    # or a StyleManager call.
    assert theme.normalize(value) == theme.DEFAULT_THEME


def test_a_config_with_no_theme_in_it_follows_the_desktop():
    assert theme.current({}) == "system"


def test_following_the_desktop_has_no_url_value():
    # None rather than "system": with no parameter the page has no data-theme
    # and its prefers-color-scheme query decides, which is what system means.
    assert theme.query_value({"theme": "system"}) is None
    assert theme.query_value({}) is None


@pytest.mark.parametrize("value", ["light", "dark"])
def test_a_chosen_theme_has_a_url_value(value):
    assert theme.query_value({"theme": value}) == value


def test_saving_a_theme_returns_what_was_stored(monkeypatch):
    written = {}
    monkeypatch.setattr(theme.config_module, "save", written.update)
    config = {}
    assert theme.save(config, "dark") == "dark"
    assert config["theme"] == "dark"
    assert written["theme"] == "dark"


def test_saving_a_theme_nobody_knows_stores_the_default_instead(monkeypatch):
    # The caller is told what landed, not what it asked for, so a settings row
    # cannot end up showing a value the config does not hold.
    monkeypatch.setattr(theme.config_module, "save", lambda _config: None)
    config = {}
    assert theme.save(config, "solarized") == "system"
    assert config["theme"] == "system"


@pytest.mark.parametrize("dark,expected", [(True, "#16171d"), (False, "#f5f5f7")])
def test_a_pane_is_painted_the_palette_it_is_loading(dark, expected):
    assert theme.pane_background(dark) == expected


@pytest.mark.parametrize("selector,dark", [(":root", False),
                                          (':root[data-theme="dark"]', True)])
def test_the_pane_colours_are_the_ones_the_pages_paint(selector, dark):
    """Pin the copy in theme.py to the stylesheet it was copied from.

    The value has to be in both places: the page gets it from `--bg`, and the
    view has to be told it before the stylesheet carrying `--bg` has arrived.
    A palette edited in one place and not the other would put ISSUE-079's flash
    back, in a colour nobody chose.
    """
    css = (pathlib.Path(theme.__file__).parent
           / "remote" / "static" / "styles.css").read_text()
    block = css.split(selector + " {", 1)[1].split("}", 1)[0]
    declared = re.search(r"--bg:\s*(#[0-9a-f]{6})", block).group(1)
    assert theme.pane_background(dark) == declared


# ------------------------------------------------------ how long ago it was

def ago(**kwargs) -> str:
    return resume_dialog._relative_time(datetime.now() - timedelta(**kwargs))


@pytest.mark.parametrize("delta,expected", [
    ({"seconds": 0}, "just now"),
    ({"seconds": 59}, "just now"),
    ({"seconds": 60}, "1m ago"),
    ({"minutes": 59}, "59m ago"),
    ({"minutes": 60}, "1h ago"),
    ({"hours": 23}, "23h ago"),
    ({"hours": 24}, "1d ago"),
    ({"days": 29}, "29d ago"),
])
def test_a_recent_session_is_dated_in_the_largest_unit_that_fits(delta, expected):
    assert ago(**delta) == expected


def test_a_session_older_than_a_month_gets_a_date():
    # "47d ago" stops meaning anything, and the list is sorted anyway.
    when = datetime.now() - timedelta(days=45)
    assert resume_dialog._relative_time(when) == when.strftime("%Y-%m-%d")


def test_a_session_from_the_future_is_not_dated_backwards():
    # A machine whose clock moved, or a transcript copied from another one.
    # Without the clamp this reads as a negative number of minutes.
    assert resume_dialog._relative_time(datetime.now() + timedelta(hours=3)) == "just now"


# ----------------------------------------------------- the settings strings

def test_a_cli_with_a_version_is_named_with_it():
    assert settings_view._cli_line(
        {"path": "/usr/bin/claude", "version": "1.2.3"}) == "/usr/bin/claude (1.2.3)"


def test_a_cli_whose_version_could_not_be_read_is_still_named():
    assert settings_view._cli_line({"path": "/usr/bin/claude"}) == "/usr/bin/claude"
    assert settings_view._cli_line(
        {"path": "/usr/bin/claude", "version": None}) == "/usr/bin/claude"


@pytest.mark.parametrize("entry", [{}, {"path": None}, {"path": ""}])
def test_a_cli_that_is_not_there_produces_no_line(entry):
    assert settings_view._cli_line(entry) == ""


def test_notifications_are_on_unless_turned_off():
    # Absent from a config written before ISSUE-030, which should keep the
    # behaviour it had rather than going quiet on upgrade.
    assert settings_view.notifications_enabled({}) is True
    assert settings_view.notifications_enabled({"notifications": True}) is True


def test_notifications_are_off_only_when_the_value_says_so():
    assert settings_view.notifications_enabled({"notifications": False}) is False


# --------------------------------------------------------- the icon repair

def test_repairing_the_icons_with_no_display_does_nothing_and_raises_nothing():
    # It runs before the window is built and is the first thing in the app to
    # ask for a display. Raising here would take the app down at startup on a
    # machine where it could otherwise report the problem.
    assert main_module._repair_libadwaita_icons() is None
