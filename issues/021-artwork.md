---
id: ISSUE-021
title: Integrate artwork
status: done
type: feature
area: gui
created: 2026-08-19
updated: 2026-08-20
related: [ISSUE-030]
---

## Summary

Integrate artwork into the software interface

## Notes & worklog

- Art is at docs/art/codinian.svg
- place on blank panel background (the "No Session Selected" message - replace terminal icon with our custom svg)
- go back the original startup state in early code version where on startup, nothing is selected and the main content pane shows the program icon and a prompt to select - in this case we'll expand it to say "Welcome to Codinian [line break and vertical space] Select Project or Sesssion"
- use icon in web view as well (replaces square with terminal greater-than symbol)

- **2026-08-20, done, and one of the four bullets turned out to be a bug.**
  The startup state had not been changed on purpose. A `Gtk.ListBox` in SINGLE
  selection mode selects its first row as soon as focus reaches it, and the
  sidebar takes focus at startup, so when ISSUE-030 added the History row above
  the Codinian/cogwheel row in the pinned bottom list, GTK selected it and the
  handler switched the content pane to the history page. The app had been
  opening on a search box, with a sidebar row highlighted, since that change.
  Nothing failed and no test covered it.

  `_open_on_welcome` now runs on an idle after construction, clears all three
  sidebar lists and shows the welcome screen. It has to be deferred rather than
  done inline: the selection happens when focus arrives, which is after
  `__init__` has returned. It also resets the content header, which the history
  handler had already retitled on its way through.

  The welcome screen is the mark, "Welcome to Codinian", and "Select a project
  or session", replacing a terminal icon and "No Session Selected". The mark is
  loaded as a `Gdk.Texture` from `docs/art/codinian.svg` rather than by icon
  name, so it does not depend on `net.higheraims.codinian` having been
  installed into the icon theme; it falls back to the old terminal icon only if
  the art is missing from the checkout. Cached, since it is 107 KB of path data
  to rasterise and every window would otherwise redo it.

  In the browser the mark replaces the square-and-chevron in the brand of all
  three pages, and the same drawing now appears above "Select a session from
  the sidebar to view its transcript", so the two clients open on the same
  thing. That greeting gets a separate `.empty-state.is-welcome` class because
  `.empty-state` is also what "Loading…" and "That transcript could not be
  read." use, and a logo above a read failure would be strange. The pages also
  gained a favicon, which they had never had.

  The brand mark is 26px rather than the 20px of the line icon it replaced.
  Rendered at 20, 24, 32 and 48 and looked at: this is a drawing of a laurel
  wreath, and below about 24px the leaves collapse into a grey blur. It carries
  its own light ground, so it needs no dark and light variants; checked against
  both palettes.

  `remote/static/codinian.svg` is a byte copy of `docs/art/codinian.svg`, which
  stays the source. Scour would take it from 113 KB to 80 KB at precision 3,
  but that shifts 8% of the pixels at 256px, and the file loads once and
  caches, so the trade was not worth a fidelity question.

  Verified with `test_welcome.py` in the scratchpad, which asserts the window
  opens on `_empty` with all three lists unselected and no title of its own in
  the content header, that the mark loads and is cached, and that selecting History
  still works, so the fix did not simply disable the row. The rendering was
  checked by screenshot on both clients and both themes.

- **2026-08-20, follow-up: "Codinian" was appearing twice across the top bar.**
  Clearing the stale history title had substituted a new one reading
  "Codinian", which sat beside the sidebar header saying the same word, above a
  welcome screen saying it a third time in large type. The content header is
  now blank on the welcome screen, which is what it was before the History row
  existed. Blank means an empty `Adw.WindowTitle`, not `None`: a header with no
  title widget falls back to the window's own title, and the window is called
  Codinian, so `None` would have produced the very thing being removed. Checked
  rather than assumed.

  The mark also appears small in the window's top-left corner. That is KWin
  drawing the window icon from the `net.higheraims.codinian` app id, which
  resolves to the installed copy of this same art. Confirmed by running the
  identical window under an app id with no installed icon, where the corner is
  empty. It is the desktop environment doing its usual job, not a third copy
  this issue introduced.

  Not verified: how the mark reads on a phone screen, and the favicon in a real
  browser tab (WebKitGTK does not show one).
