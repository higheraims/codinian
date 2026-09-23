---
id: ISSUE-063
title: Start new session via remote web interface
status: done
type: feature
area: gui
created: 2026-09-22
updated: 2026-09-23
related: [ISSUE-020, ISSUE-043]
---

## Summary

Start new session from remote web interface

The control existed; the page it lives on could not be reached. `project.js`
has had a **New session** button on a project's Sessions tab since the project
workspace landed, and it POSTs to `/api/projects/{id}/sessions`. But nothing in
`index.html` linked to `project.html`. The only links to it were from inside
itself and from `window.py:832`, which the desktop app uses to open it in a
pane. So a browser landed on the transcript page with a session list and no
other control, and an empty session list meant nothing to click at all.

## Acceptance / done-when

- The transcript page has a route to the project view, so a browser that opens
  on an empty session list has somewhere to go.
- The route back exists, so reaching projects is not a one-way door.
- Neither link appears in the desktop panes, which reach both views from the
  GTK sidebar and cannot navigate out of an embedded page.

## Notes & worklog

- 2026-09-23: A **Projects** link on the sidebar's Sessions heading
  (`index.html`), and an **All sessions** link on the project topbar
  (`project.html`). Both are plain anchors. No token on either: the two pages
  read the same `codinian_token` key in localStorage, and a token in an href
  ends up in screenshots.
- **Hidden in the panes for free.** `styles.css:1746` already hides `#topbar`
  and `#sidebar` under `#app.embed-mode`, and `project.css:709` hides
  `#topbar`, so neither link needs JavaScript to know where it is. Verified by
  loading both pages with `embed=1` in headless Chromium.
- Labelled "All sessions" rather than "Sessions" because the project view
  already has a Sessions **tab**, which is this project's sessions where the
  link is every session.
- Reaching `project.html` also unlocks Files, Issues and Git from a phone.
  They were written and working, and equally unreachable.
- Nothing was wrong with the API. `POST /api/projects/{id}/sessions` with an
  empty body starts a session in the project root under the repo's default
  permission mode, which is what the button has always promised.

## Resolution

Two anchors and the CSS to place them. The feature was already built and
untested only in the sense that no one could get to it.
