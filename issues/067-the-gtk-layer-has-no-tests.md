---
id: ISSUE-067
title: The seven modules that import gi have no tests
status: open
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

## Resolution


