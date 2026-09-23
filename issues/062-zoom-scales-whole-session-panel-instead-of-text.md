---
id: ISSUE-062
title: Zoom scales whole session panel instead of text
status: done
type: bug
area: gui
created: 2026-09-22
updated: 2026-09-23
related: [ISSUE-006]
---

## Summary

Pinch to zoom on trackpad scales the whole session panel within its envelope rather than scaling the text. This is on Codinian Desktop, not remote web view.

## Acceptance / done-when

- A trackpad pinch over a session pane changes the text size, reflowing the
  page, rather than scaling it behind a viewport that stays put.
- The handler stops raising on every event delivered to a pane.

## Notes & worklog

- 2026-09-23: `_redirect_pinch_to_zoom` (`window.py:767`) was written to do
  exactly what this issue asks for and never ran past its first line. The
  journal for one 13 hour run of pid 6278 holds **175,686** copies of:

  ```
  File "codinian/window.py", line 790, in on_event
      if event.get_event_type() != Gdk.EventType.TOUCHPAD_PINCH:
  AttributeError: 'NoneType' object has no attribute 'get_event_type'
  ```

  Not one per pinch. One per event of any kind delivered to a pane, so a mouse
  move across a transcript was logging tracebacks at a few per second.

- **Cause.** `GtkEventControllerLegacy::event` declares its argument as
  `GdkEvent*`, a fundamental type rather than a GObject, and PyGObject 3.56.3
  passes None in its place. The fix asks the controller instead:
  `controller.get_current_event()`. Nothing else in `window.py` hit this,
  because every other controller is a `Gtk.GestureClick` whose signals carry
  plain doubles.

- **Measured rather than assumed**, since the argument being None and the
  replacement working are both claims about PyGObject on this machine. A probe
  under `gtk4-broadwayd` with a `WebKit.WebView` and the same controller, driven
  by mouse moves synthesised through Chromium's debugging protocol, reported
  `signal_arg_none=30 get_current_event_ok=30` over 30 events, and the returned
  object was a `Gdk.MotionEvent` rather than a bare `Gdk.Event`. The downcast is
  what matters for the rest of the handler, which calls `get_gesture_phase()`
  and `get_pinch_scale()`; those live on `Gdk.TouchpadEvent`, not on the base
  class.

- **Not verified here:** that a real trackpad pinch now zooms. Broadway has no
  way to synthesise `TOUCHPAD_PINCH`, so that is a hardware check. The handler
  now reaches the branch, which is what was broken.

- The touchscreen half of the docstring still stands and was not touched. Those
  arrive as touch events rather than `TOUCHPAD_PINCH`, and viewport zoom is the
  gesture people expect there.

- Takes effect on restart. The running instance is still the old code.

## Resolution

One line, plus the comment explaining why it cannot be written the obvious way.
The feature was fully implemented; every event reaching it was None.
