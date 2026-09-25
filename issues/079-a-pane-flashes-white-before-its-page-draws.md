---
id: ISSUE-079
title: A pane flashes white before its page draws
status: done
type: bug
area: gui
created: 2026-09-24
updated: 2026-09-24
related: [ISSUE-030, ISSUE-070]
---

## Summary

Reported: "when clicking on a project, before the project area opens in the
desktop app, the background flashes white. No flash at all would be good, but
when you're in dark mode at night the sudden flash is painful."

A pane is built on the click that first selects it, not at startup: `_show_project`
calls `_build_project_pane` the first time, which makes a `WebKit.WebView`, adds
it to the content stack and shows it immediately. Nothing has loaded yet, and a
view that has not been told otherwise paints opaque white until its page draws.
The stack's CROSSFADE transition blends that white in rather than hiding it.

Measured, not assumed:

```
WebKit.WebView().get_background_color()   ->  1.0 1.0 1.0 1.0  (opaque white)
project.html over loopback, warm WebKit   ->  started 257 ms, committed 258 ms,
                                              finished 363 ms
```

So between a quarter and a third of a second of white, in a window whose own
background is #16171d. Sessions and History are built the same way and had the
same flash; projects are where it was noticed.

## Steps to reproduce

Theme dark (or a dark desktop with theme following it), start the app, click a
project that has not been opened since launch.

## Acceptance / done-when

- The pane is the palette's background from the moment it appears.
- Light mode gets the light background, not a dark one, and "follow the desktop"
  resolves to whichever the desktop is on.
- A theme change reaches views that are already built, not only the next one.

## Notes & worklog

- `webkit_web_view_set_background_color` is the API for exactly this: the colour
  drawn before the contents are rendered, superseded by the page's own once the
  page has one. The page already has one, `--bg` in `styles.css`, so the two
  values have to agree; `test_the_pane_colours_are_the_ones_the_pages_paint`
  reads the stylesheet and pins them together.

- The choice comes from `Adw.StyleManager.get_default().get_dark()` rather than
  from the stored theme, because that answers for all three settings at once:
  `apply_to_shell` has already forced light or dark onto it, and "system" leaves
  it reporting the desktop. It is also the value WebKitGTK derives
  `prefers-color-scheme` from, which is what a pane loaded with no `theme=`
  parameter paints by, so the two cannot disagree.

- No flash at all would mean holding the stack on the old pane until the new one
  has painted. `WebKit` has no first-paint signal, only `load-changed`, so the
  wait would be at least the 363 ms above with nothing happening on screen after
  a click. Not worth it against a pane that comes up in the right colour and
  fills in.

- **Not verified on a screen.** Two headless routes were tried and neither can
  see this particular pixel. Under `gtk4-broadwayd` WebKit has no GL and draws
  nothing at all before its first content frame, so a view whose background was
  set to pure red never showed red through a 3 second stall. A nested
  `kwin_wayland --virtual` does composite with GL, but `org.kde.KWin.ScreenShot2`
  refuses a caller that is not an allowlisted binary, and `Gtk.WidgetPaintable`
  over a `WebKit.WebView` renders fully transparent. What is measured is the
  default colour, the load timings, and the colour the fix puts on a real view.

## Resolution

`theme.pane_background`, a `PANE_BACKGROUNDS` pair copied from `styles.css`, and
`CodinianWindow._apply_pane_background` called from `_build_webview_pane` before
the load, so it covers project, session and History panes alike.
`_on_theme_changed` puts the new palette on the views already built, since the
colour is a property of the view and does not follow the page over a reload.

Four tests. Two on the mapping, one of which reads `styles.css` so the copy
cannot drift from the original. Two in `tests/test_window_gtk.py`, on a real
`WebKit.WebView` under broadway: one records that an untouched view is `#ffffff`,
which is the bug, and one that `_apply_pane_background` under FORCE_DARK leaves
it `#16171d`. Full suite: 784 passed.
