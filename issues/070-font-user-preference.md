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
- The size is reachable without a touchpad.
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

- Not covered here: a keyboard shortcut. Ctrl+= and Ctrl+- would be the
  obvious companions to the pinch and are a separate small piece of work.

## Resolution

`pane_zoom` in the config, applied by `_build_webview_pane` so every pane opens
at it, and by `_apply_pane_zoom` so a change reaches the panes already open.
Set either by a pinch on any pane or by "Text size in panes" in Settings, and
the same size for all of them. The GTK shell is untouched and follows the
desktop.
