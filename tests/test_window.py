"""The parts of `window.py` that do not need a window.

Everything here runs with no display and in milliseconds, because none of it
opens one: importing `codinian.window` pulls in GTK but opening a display is
what `Gtk.Window.present()` does, and nothing below presents anything. The
methods that read only their own attributes are called unbound against a stand
-in `self`, which is cheaper and clearer than building a real window to reach
four lines of string handling.

`tests/test_window_gtk.py` is the other half: a real window, under a real GTK
display, with real events delivered into it. It costs seconds rather than
milliseconds and needs two programs this file does not (ISSUE-067).
"""

from __future__ import annotations

import types

import pytest

from codinian import window
from codinian.session import Session, SessionStatus
from codinian.window import CodinianWindow


# ------------------------------------------------------------- pane zoom

@pytest.mark.parametrize("level,expected", [
    (0.01, window.PANE_ZOOM_MIN),
    (window.PANE_ZOOM_MIN, window.PANE_ZOOM_MIN),
    (1.0, 1.0),
    (window.PANE_ZOOM_MAX, window.PANE_ZOOM_MAX),
    (99.0, window.PANE_ZOOM_MAX),
])
def test_a_zoom_level_is_held_inside_the_usable_range(level, expected):
    assert window._clamp_pane_zoom(level) == expected


# --------------------------------------------------- the pinch state machine

# Broadway delivers real motion and button events to a headless widget but
# cannot synthesise TOUCHPAD_PINCH, so this is the only way the branch that
# decides what a pinch does is reachable at all (ISSUE-067).

def step(phase, scale=1.0, start=None, current=1.0):
    from gi.repository import Gdk
    return window._pinch_zoom_step(
        getattr(Gdk.TouchpadGesturePhase, phase), scale, start, current)


def test_the_beginning_of_a_pinch_records_the_level_it_started_from():
    # Nothing is applied yet: BEGIN carries a scale of 1.0 and applying it
    # would be a no-op write on every gesture.
    assert step("BEGIN", scale=1.0, start=None, current=1.4) == (1.4, None)


def test_an_update_scales_the_level_the_gesture_started_from():
    # Not the pane's current level. get_pinch_scale is cumulative from BEGIN,
    # so multiplying the running level by it compounds the gesture.
    assert step("UPDATE", scale=1.5, start=1.0) == (1.0, 1.5)
    assert step("UPDATE", scale=1.5, start=1.5) == (1.5, 2.25)


def test_a_pinch_that_would_leave_the_usable_range_is_clamped():
    assert step("UPDATE", scale=10.0, start=1.0)[1] == window.PANE_ZOOM_MAX
    assert step("UPDATE", scale=0.01, start=1.0)[1] == window.PANE_ZOOM_MIN


def test_the_end_of_a_pinch_keeps_what_the_last_update_set():
    # No level to apply, and the start is dropped so the next BEGIN reads the
    # level this gesture left behind.
    assert step("END", start=1.0, current=2.0) == (None, None)


def test_a_cancelled_pinch_puts_the_level_back():
    assert step("CANCEL", start=1.0, current=2.4) == (None, 1.0)


def test_a_cancel_with_no_gesture_behind_it_changes_nothing():
    assert step("CANCEL", start=None, current=2.4) == (None, None)


def test_an_update_with_no_beginning_behind_it_changes_nothing():
    # What arrives when the gesture was already under way before this pane
    # existed. Scaling from an unknown level would jump the pane.
    assert step("UPDATE", scale=2.0, start=None, current=1.0) == (None, None)


def test_a_whole_gesture_zooms_once_and_settles():
    start, level = step("BEGIN", current=1.0)
    assert level is None
    for scale, expected in [(1.2, 1.2), (1.6, 1.6), (2.0, 2.0)]:
        start, level = step("UPDATE", scale=scale, start=start)
        assert level == pytest.approx(expected)
    assert step("END", start=start) == (None, None)


# ------------------------------------------------------- stored window size

@pytest.mark.parametrize("value", [None, "1280", 0, -1, 1.5, True, False])
def test_a_stored_dimension_that_is_not_a_usable_size_falls_back(value):
    # The file is hand-editable, and a window sized 0 or "1280" cannot be
    # shown. True is excluded explicitly because bool is an int.
    assert window._positive_int(value, 800) == 800


def test_a_usable_stored_dimension_is_kept():
    assert window._positive_int(1280, 800) == 1280


# ----------------------------------------------------------- sidebar paths

def test_the_home_directory_itself_is_written_as_a_tilde(tmp_path, monkeypatch):
    monkeypatch.setattr(window.Path, "home", classmethod(lambda cls: tmp_path))
    assert window._short_path(str(tmp_path)) == "~"


def test_a_path_under_home_is_shortened(tmp_path, monkeypatch):
    monkeypatch.setattr(window.Path, "home", classmethod(lambda cls: tmp_path))
    assert window._short_path(f"{tmp_path}/Projects/codinian") == "~/Projects/codinian"


def test_a_path_outside_home_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(window.Path, "home", classmethod(lambda cls: tmp_path))
    assert window._short_path("/etc/hosts") == "/etc/hosts"


def test_a_sibling_of_home_is_not_mistaken_for_a_child(tmp_path, monkeypatch):
    # The prefix test has to include the separator: /home/ntyler2 starts with
    # /home/ntyler.
    monkeypatch.setattr(window.Path, "home", classmethod(lambda cls: tmp_path))
    other = f"{tmp_path}2/notes"
    assert window._short_path(other) == other


# --------------------------------------------------------- the pane's URL

def fake_window(**config):
    """A stand-in `self` carrying only what the method under test reads."""
    fake = types.SimpleNamespace(
        _config={"port": 8787, "token": "tok", "theme": "system", **config},
        _CLOSE_WARNINGS=CodinianWindow._CLOSE_WARNINGS,
        _project_meta={},
    )
    # _pane_url calls it, so it has to be bound rather than passed.
    fake._theme_param = lambda: CodinianWindow._theme_param(fake)
    return fake


def test_a_pane_talks_to_loopback_whatever_the_server_is_bound_to():
    # The bind address is the server's business. A pane that followed it would
    # try to reach the app over the network it is already inside.
    url = CodinianWindow._pane_url(fake_window(bind="0.0.0.0"), "abc123")
    assert url.startswith("http://127.0.0.1:8787/?embed=1&session=abc123")


def test_a_pane_url_carries_the_token():
    assert "token=tok" in CodinianWindow._pane_url(fake_window(), "abc123")


def test_a_token_with_url_characters_in_it_is_escaped():
    # `+` and `=` are what matter: unescaped, a `+` in a query value decodes
    # back as a space and the token no longer matches. `/` is left alone
    # because it is unambiguous inside a query value. Defensive either way,
    # since `secrets.token_urlsafe` produces neither.
    url = CodinianWindow._pane_url(fake_window(token="a+b=c"), "s")
    assert "token=a%2Bb%3Dc" in url


def test_following_the_desktop_theme_adds_no_parameter():
    # Empty rather than theme=system, so the common case produces the URL it
    # always did and the page falls through to prefers-color-scheme.
    assert CodinianWindow._theme_param(fake_window(theme="system")) == ""


@pytest.mark.parametrize("value", ["light", "dark"])
def test_a_chosen_theme_is_named_in_the_pane_url(value):
    assert CodinianWindow._theme_param(fake_window(theme=value)) == f"&theme={value}"


# ------------------------------------------------- what closing warns about

def warning(status, kind="sdk"):
    session = Session(id="s1", kind=kind)
    session.sdk_status = SessionStatus(status) if kind == "sdk" else session.status
    return CodinianWindow._close_warning(fake_window(), session)


@pytest.mark.parametrize("status", [SessionStatus.DONE.value, SessionStatus.ERROR.value])
def test_a_finished_session_is_closed_without_a_warning(status):
    # There is nothing left to stop, so a confirmation would be a dialog
    # asking about nothing.
    assert warning(status) is None


@pytest.mark.parametrize("status", [
    SessionStatus.WORKING.value,
    SessionStatus.AWAITING_APPROVAL.value,
    SessionStatus.AWAITING_INPUT.value,
    SessionStatus.INITIALIZING.value,
])
def test_every_live_status_says_what_closing_stops(status):
    assert warning(status)


def test_a_status_with_no_wording_of_its_own_still_warns():
    # A status added to the protocol and not to _CLOSE_WARNINGS must not close
    # a running session silently.
    session = Session(id="s1", kind="sdk")
    session.sdk_status = SessionStatus.WORKING
    fake = fake_window()
    fake._CLOSE_WARNINGS = {}
    assert CodinianWindow._close_warning(fake, session) == "This session is still running."


def test_a_terminal_session_is_warned_about_as_a_terminal():
    # Its status comes from how long it has been quiet, which says nothing
    # about what the shell is doing, so the wording cannot promise anything.
    text = warning(SessionStatus.WORKING.value, kind="terminal")
    assert "terminal" in text
