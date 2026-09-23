---
id: ISSUE-067
title: The seven modules that import gi have no tests
status: done
type: chore
area: tools
created: 2026-09-23
updated: 2026-09-23
related: [ISSUE-044, ISSUE-062, ISSUE-043]
---

## Summary

620 tests cover everything except the part with the display in it. There is no
`tests/test_window.py`, and none for `settings_view`, `session_dialog`,
`resume_dialog`, `remote_panel`, `theme` or `main`. That is exactly the seven
modules [[ISSUE-043]] lists as importing `gi`.

[[ISSUE-062]] is what the gap costs. `_redirect_pinch_to_zoom` raised
`AttributeError` on its first line for every event delivered to a pane, logged
175,686 tracebacks in one 13 hour run, and was found by reading the journal
rather than by anything failing.

## Acceptance / done-when

- `window.py` has a test file, and the pinch controller is one of the cases.
- It runs in CI with no display and no packages that need root to install.
- A handler that raises on every event fails the suite rather than filling the
  journal.

## Notes & worklog

- 2026-09-23: **The technique already works**, which is what makes this worth
  doing now rather than keeping as a wish. Written and run while diagnosing
  [[ISSUE-062]]:

  - `gtk4-broadwayd :N` gives a display server with no display. `GDK_BACKEND=broadway`
    and `BROADWAY_DISPLAY=:N` put a real `Gtk.ApplicationWindow` with a real
    `WebKit.WebView` in it.
  - Headless Chromium with `--remote-debugging-port` connects to the broadway
    page, and `Input.dispatchMouseEvent` over CDP delivers real input to the
    GTK widget. `aiohttp` is already a dependency and speaks the WebSocket, so
    the driver needs nothing new.
  - The probe reported `signal_arg_none=30 get_current_event_ok=30` across 30
    mouse moves, which is both halves of the ISSUE-062 diagnosis measured
    rather than argued.

- Xvfb and xdotool would be the obvious alternative and are not installed;
  installing them needs root. Broadway needs nothing, which matters more for CI
  than for this machine.

- **What broadway cannot do is synthesise `TOUCHPAD_PINCH`**, so the pinch
  gesture itself stays a hardware check. The test worth having is the one that
  would have caught the real bug anyway: that the controller is called, that it
  does not raise, and that `get_current_event()` returns a typed event.

- Worth deciding as part of this: whether these run in the same `pytest`
  invocation as the other 620 or in a marked group. They start two processes
  and take seconds rather than milliseconds.

- 2026-09-23: **The decision the note above asked for: they run in the same
  invocation, and carry a `gtk` mark.** A test nobody runs is the state this
  ticket is about. The whole suite is 718 tests in 9.5 seconds, of which the
  GTK file is 6 tests and about 6 of those seconds; `-m "not gtk"` leaves 712
  in 2.5 for a tight edit loop.

- **The window has to open in a child process, which was measured rather than
  chosen.** `from gi.repository import Gtk` runs `Gtk.init()`, so the display
  is opened at import. Setting `GDK_BACKEND=broadway` after that import is
  ignored: a probe that did so reported `GdkWaylandDisplay`, meaning it had
  connected to the developer's own compositor. pytest imports every test module
  during collection and `tests/test_window.py` imports `codinian.window`, so in
  one process the window would open on whatever the developer is logged into,
  and in CI it would fail differently. `tests/gtk_probe.py` gets the
  environment set before its first import, and takes the GTK and WebKit
  processes out of the runner with it.

- **The acceptance criterion about a raising handler was checked by putting the
  bug back.** With `event = _event` and no None guard, which is the ISSUE-062
  shape, the probe reports 30 events and 30 copies of `AttributeError:
  'NoneType' object has no attribute 'get_event_type'`, and
  `test_no_handler_on_a_pane_raises` fails. PyGObject prints an exception
  raised in a signal handler and carries on, so the probe records
  `sys.excepthook`, which is what turns 175,686 lines in the journal into a
  number.

  **The first attempt at that injection is worth recording, because it passed.**
  Keeping the `if event is None` guard while reading the signal argument raises
  nothing and does nothing: every event returns False, pinch-to-zoom is dead,
  and every count in the probe stays clean. `sys.excepthook` cannot see a
  handler that fails by doing nothing. The nearest guard available is
  `test_the_signal_argument_is_never_the_event`, which asserts
  `signal_arg_none == events` and so states in the suite that the argument can
  never be used. The limit is written into the module docstring rather than
  left for the next reader to find.

- **The pinch arithmetic came out of the closure** as `_pinch_zoom_step`, a
  pure function of phase, cumulative scale, starting level and current level.
  Broadway cannot synthesise `TOUCHPAD_PINCH`, so this was the only way to
  reach the branch that decides what a pinch does. The extraction is equivalent
  in all six cases the original handled, including the two that fell through
  its `else`: an UPDATE with no BEGIN behind it, and a CANCEL with nothing to
  restore. Nine tests cover it, including a whole gesture from BEGIN to END.

- **Everything else needs no display and costs milliseconds.** The methods that
  read only their own attributes are called unbound against a
  `types.SimpleNamespace` standing in for `self`, which reaches `_pane_url`,
  `_theme_param` and `_close_warning` without building a window.

- The child inherits conftest's sandboxed `HOME`, which moves the user
  site-packages directory and hid `aiohttp` from it. Handing over the parent's
  `sys.path`, resolved before conftest repointed `HOME`, gives the child the
  parent's imports without letting it out of the sandbox. Chromium gets an
  explicit `--user-data-dir` in a temp directory, removed at exit as well as
  inline, because its renderer processes outlive the terminate by long enough
  for a `rmtree` to leave the directory behind.

- **The other six modules got their logic covered too**, since the title counts
  seven. `theme`, `resume_dialog._relative_time`, `settings_view._cli_line` and
  `notifications_enabled`, and `main._repair_libadwaita_icons` on a machine
  with no display. Nothing that builds a dialog or a preferences page: that is
  layout, and asserting a row exists says only that someone wrote the line that
  adds it.

  One of those tests had to be rewritten after it was found to prove nothing.
  "Imports without a display" passes on a desktop whether or not a display is
  needed, because GDK finds the Wayland socket through `XDG_RUNTIME_DIR` with
  `DISPLAY` and `WAYLAND_DISPLAY` both unset. It now runs in a child with all
  three stripped and asserts `Gdk.Display.get_default() is None`.

## Resolution

Three files and a probe. `tests/test_window.py` is the display-free half of
`window.py`, `tests/test_window_gtk.py` drives a real window through
`tests/gtk_probe.py`, and `tests/test_gtk_modules.py` covers the logic in the
other six modules. 631 tests to 718.

The pinch controller is covered from both sides: the arithmetic as a pure
function, and the handler as something a real event stream is put through. What
stays a hardware check is the gesture itself, because broadway cannot
synthesise `TOUCHPAD_PINCH`.
