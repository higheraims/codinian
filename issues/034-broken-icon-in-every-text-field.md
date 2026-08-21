---
id: ISSUE-034
title: Every text field draws a broken-image icon under the Breeze icon theme
status: done
type: bug
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-014, ISSUE-030]
---

## Summary

Every `Adw.EntryRow` in the app shows a red broken-image square at its right
edge whenever the row does not have focus. It is on Name and Working Directory
in the new-session dialog, on Save as Template, and on the entry rows in
Settings. Found while screenshotting the new-session dialog to check the
ISSUE-014 Template group; the Template group itself is fine.

Not a Codinian bug. Four bare libadwaita rows in a window with none of this
project's code reproduce it exactly.

## Acceptance / done-when

- Text fields draw either the intended edit indicator or nothing at all.
- Whatever the fix is, it does not depend on the user's icon theme being
  Adwaita, since the machine this runs on uses Breeze.

## Notes & worklog

- **2026-08-20, what it actually is.** The rows request two icons by name:
  `adw-entry-edit-symbolic` (the pencil shown on an unfocused row) and
  `adw-entry-apply-symbolic`. Neither resolves, so GTK substitutes
  `image-missing`, which in Breeze is a red box around a red circle-slash.
  Zooming the screenshot to 10x confirms that is the glyph being drawn.

  These are libadwaita's own private icons, not theme icons, so a missing
  `adwaita-icon-theme` is not the explanation. Both packages are installed
  (`adwaita-icon-theme-50.0-1.fc44`), and both names are absent from Breeze and
  from Adwaita alike, as they should be.

  What makes this odd is that the icons are present and the path is registered.
  libadwaita 1.9.3 registers `/org/gnome/Adwaita/icons` as an icon resource
  path, and enumerating that resource lists
  `scalable/actions/adw-entry-edit-symbolic.svg` and
  `scalable/actions/adw-entry-apply-symbolic.svg` -- the exact layout
  `add_resource_path` documents. `Gtk.IconTheme.has_icon` still returns False
  for both, under `breeze-dark`, `Adwaita` and `hicolor`.

- **2026-08-20, root cause, and the fix.** GTK 4.22.4 finds an icon in a
  registered resource path only when the file sits **directly** at that path.
  The documented `<path>/scalable/actions/<name>.svg` layout is not found.

  Isolated with three purpose-built GResources registered in one process,
  differing only in layout: a flat one resolves, a nested `scalable/actions/`
  one does not, and a nested one carrying its own `hicolor.index.theme` does
  not either. So it is the nesting, not a missing index. GTK's own icon
  resource is flat, which is why GTK's icons work and libadwaita's do not, and
  why this is invisible until something asks for an `adw-` name. Confirmed the
  lookup machinery is otherwise healthy under Breeze: `function-linear-symbolic`,
  `switch-off-symbolic` and `gesture-pinch-symbolic` are in no on-disk theme on
  this machine and all resolve, from GTK's flat resource.

  The repair is one line in `main.py`, naming the inner directory as a resource
  path of its own:

      Gtk.IconTheme.get_for_display(display).add_resource_path(
          "/org/gnome/Adwaita/icons/scalable/actions")

  It needs no files and no build step, because libadwaita's resource is already
  registered in the process; the icons drawn are the ones libadwaita intended
  rather than substitutes. It stays harmless when the upstream bug is fixed: it
  names a directory that either exists, in which case the icons were already
  right, or does not, in which case the lookup simply fails as it does today.

- **2026-08-20, the fix that was tried first, and why it is not the one.**
  Shipping the two icons in a minimal `hicolor` tree and adding a search path
  is the obvious repair, and it is a trap twice over.

  `add_search_path` on the display's icon theme has no effect on lookups at
  all. The path lands in `get_search_path()` and nothing resolves through it.
  This is what made the first attempt at this issue report that supplying the
  icons "changed nothing" -- the directory was fine, the call was not.

  `set_search_path` does work, but only with our directory **first**, and that
  is worse than the bug. Measured: after prepending a minimal `hicolor` tree,
  `net.higheraims.codinian` and `firefox` stop resolving, because GTK takes one
  `hicolor` rather than merging across base directories, so a small private
  copy shadows the real one. The app would have lost its own icon to fix a
  pencil, and a screenshot of the text field would have looked perfect.
  `test_icons.py` asserts the app icon survives, for that reason.

  Verified: the same three bare libadwaita rows, run twice in the same
  session, 144 red image-missing pixels before the repair and 0 after, with the
  pencil drawn in their place; and the real new-session dialog, 0. Note both
  runs must not overlap -- the first capture was of the earlier window still on
  screen, which reported the fix as having done nothing.

- Reproduction, needing nothing from this repo: a GTK4 window holding an
  `Adw.EntryRow`, an `Adw.PasswordEntryRow` and an `Adw.EntryRow` with a suffix
  button. The focused empty row shows nothing; the unfocused ones show the
  square. The scripts are in this session's scratchpad as `shot_bare.py` and
  `shot_fixed_rows.py`.

- Worth reporting upstream. It is a GTK bug by the letter of the
  `gtk_icon_theme_add_resource_path` documentation, which describes exactly the
  layout libadwaita uses; whether GTK or libadwaita moves is for them to say.
  Not filed from here.
