---
id: ISSUE-068
title: Improve project view in desktop
status: done
type: feature
area: gui
created: 2026-09-23
updated: 2026-09-23
related: []
---

## Summary

Projects list is growing, leaving not much room for sessions to be listed without scrolling. Previously we tightened the text displayed for projects, which helped, but now it's getting cramped again. Reimagine the interface based on the functions we have now to make better use of screen real estate and preserve usability.

## Acceptance / done-when

- Both lists are reachable without either one squeezing the other out.
- The list in front gets the whole sidebar height.
- A session starting or finishing is visible without switching to its list.
- Nothing about the two lists' contents or actions is lost.

## Notes & worklog

- 2026-09-23: **The cramping, measured.** A project row is 44px under the
  `codinian-compact` rule, so fourteen projects is 626px. The sidebar's scroll
  area in an 800px window is about 664px once the header bar and the pinned
  History and Codinian rows are taken off. Projects took 94% of it and Sessions
  got the rest, which is less than one row.

- **Two tabs rather than two sections**, per the user: the same shape the
  project workspace uses in the browser. `Adw.InlineViewSwitcher` over an
  `Adw.ViewStack`, which is what `settings_view.py` already uses and what
  `project.css` draws, so the three read as one interface. Each tab scrolls
  itself, so the one in front gets the full height rather than its share of it.

- **The switcher went in the sidebar's own header bar**, which until now held a
  title and nothing else. A row of its own was tried first and did not fit: at
  the old 200px minimum width the two tabs plus a tab's action buttons came to
  more than the width, and libadwaita drew "Proj…" and "Sess…". The header is
  full width and costs no vertical space at all.

  That is where the word "Codinian" was. The welcome screen says it in large
  type and the row pinned at the bottom of the sidebar says it beside the
  version, so it was the third copy.

- **The sidebar had to get wider, which was measured rather than guessed.** The
  header asks for 268px on the Projects tab and 364px on the Sessions tab,
  where the longer label sits beside a Resume and a New button. 300/200 became
  370/300. It costs the content pane 60px and gives some back: at 300 the
  longest name here drew as "Reformation-Hymnal-for-P…".

- **The session count is in the tab's label, after a badge turned out to draw
  nothing.** `Adw.ViewStackPage.badge_number` is the obvious way to do it and
  `Adw.InlineViewSwitcher` ignores it: the switcher reports the same 176px
  natural width with a badge of 3 set as with none, where the title going from
  "Sessions" to "Sessions (3)" takes it to 196. Only `Adw.ViewSwitcher`, the
  pill-shaped one the rest of the app does not use, renders a badge.

- **A session started from the browser updates the count but does not pull the
  tab.** `_adopt_session` already refused to steal the selection for the same
  reason. Creating one here does bring the Sessions tab forward, because it
  selects the new row and a selection on the tab behind is a highlight nobody
  can see.

- `_section_heading` went with the headings it built, having no callers left,
  and `_heading_button` became `_action_button` because there is no heading for
  it to sit on any more.

- **Checked in the running app, not only in tests.** A second instance under
  `gtk4-broadwayd` with its own `CODINIAN_APP_ID` and a sandboxed home, driven
  through headless Chromium: the Projects tab lists twelve of the fourteen
  without scrolling, three sessions started through the API take the label to
  "Sessions (3)" while leaving the Projects tab in front, and clicking the tab
  swaps both the list and the buttons with nothing ellipsised.

  The app-id override earned itself here. Without it the second instance hands
  off to the running one over D-Bus and exits 0, which looks exactly like a
  window that failed to open.

## Resolution

Projects and Sessions are two tabs in the sidebar's header bar, each scrolling
its own full height, with that tab's actions beside the switcher and the
session count in the Sessions label. The sidebar widened to 370/300 to hold the
header, measured against what it asks for rather than estimated.
