---
id: ISSUE-070
title: Font user preference
status: done
type: feature
area: gui
created: 2026-09-23
updated: 2026-09-23
related: []
---

## Summary

Enable user preference for font size in the various parts of the UI. We should not make this too complicated. The pinch to zoom is ok but it only applies to one panel at a time and is not remembered for other panels.

## Acceptance / done-when

- One size for every transcript and project pane, not one per pane.
- A pinch is remembered, including by panes opened afterwards and by the app
  after a restart.
- The size is reachable without a touchpad, by the keys a browser
  uses for the same thing.
- A hand-edited config file cannot open a pane at a size the controls could
  not produce.

## Notes & worklog

- 2026-09-23: **Scope settled with the user before building**: the transcript
  and project panes only, with the GTK sidebar, dialogs and Settings window
  left following the desktop's font size, which is where a preference for an
  application's own chrome belongs. The browser client keeps its browser's
  zoom, which Chrome and Safari already remember per site.

- **One stored value, `pane_zoom`, a multiplier rather than a point size.**
  That is what `WebKit.WebView.zoom_level` takes, so there is nothing to
  convert and the pinch gesture and the Settings row drive the same number. The
  range, the clamp and the reader moved from `window.py` to `config.py`, since
  the Settings page needs all three and a preferences page importing the main
  window is the wrong direction.

- **A pinch persists when the gesture settles, not while it runs.** An UPDATE
  stream is many events and each one would rewrite the config file. Only the
  pane under the fingers follows the gesture, because the others are behind it
  in the stack; on END the size goes to every pane and to the file at once.
  CANCEL counts as settled too: `_pinch_zoom_step` has already put the level
  back by then, so saving there is what stops the file disagreeing with the
  screen.

- **Written to `_config` by `_apply_pane_zoom`, not only to disk.**
  `_build_webview_pane` reads the config, so that is what makes a pane opened
  later come up at the same size as the ones already on screen. It is the half
  of "not remembered for other panels" that is not about restarts.

- The Appearance group lost its description. It said the setting "applies to
  the window and to the transcript and project panes inside it", which is true
  of the theme and false of the text size, so each row carries its own scope
  now.

- **Checked in the running app across a restart.** A second instance under
  `gtk4-broadwayd` with its own `CODINIAN_APP_ID` and a sandboxed home, driven
  through headless Chromium: the row reads 100, eight presses of + take it to
  140, `pane_zoom: 1.4` lands in the config file, and a session pane opened
  after the change comes up enlarged while the sidebar beside it does not
  move. Killed and restarted, a session that did not exist when the size was
  set still opens at 140.

- **Ctrl+=, Ctrl+- and Ctrl+0**, added straight after the rest at the user's
  request. `Gio.SimpleAction`s on the window rather than the application,
  since they act on this window's panes and `Gtk.ApplicationWindow` is already
  a `Gio.ActionMap` under the `win.` prefix. Each is bound to the shifted and
  keypad spellings as well: `<Ctrl>equal` is what an unshifted press delivers
  and `<Ctrl>plus` what a shifted one does, and binding one loses half the
  keyboards.

  Ten points a press, not the five the Settings row steps in, so a size worth
  having takes a few presses rather than a dozen. Still a multiple of five, so
  a size reached by keyboard is one that row can also show.

  **The risk worth naming was whether the key reaches the window at all.** A
  `WebKit.WebView` fills the pane and handles its own keys, so an accelerator
  it swallowed would have left the shortcut working everywhere except where it
  is wanted. Measured with real key events through the window manager, not
  reasoned about: with the pane focused, four presses of Ctrl+= take
  `pane_zoom` from 1.0 to 1.4, two of Ctrl+- to 1.2, and Ctrl+0 to 1.0. It
  also fires with the GTK Settings pane focused, where the row updates to 130
  under three presses.

  A press writes the file, unlike a pinch, which waits for the gesture to
  settle: a press is already one event and the next may be minutes away.

- **`clamp_pane_zoom` rounds to a whole percent**, which the keyboard made
  necessary. Three presses is `1.0 + 0.1 + 0.1 + 0.1`, which is
  1.3000000000000003 in binary floating point and went into the config file
  that way. A pinch multiplies and lands on numbers no less arbitrary. One
  percent is finer than anyone can see in a pane and is what the Settings row
  counts in.

- Two false readings came from the same thing while testing this, and both
  looked like bugs in the code. A second instance started while the first is
  still shutting down hands off to it over D-Bus and exits 0, so a "restart"
  silently keeps running the old build: once that showed as four presses
  moving seven steps, and once as the rounding appearing not to work. Compare
  the process start time against the source mtime before believing a live
  measurement.

## Resolution

`pane_zoom` in the config, applied by `_build_webview_pane` so every pane opens
at it, and by `_apply_pane_zoom` so a change reaches the panes already open.
Set by a pinch on any pane, by Ctrl+= / Ctrl+- / Ctrl+0, or by "Text size in
panes" in Settings, and
the same size for all of them. The GTK shell is untouched and follows the
desktop.
