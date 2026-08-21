---
id: ISSUE-031
title: Dropdowns render as white boxes with white text
status: done
type: bug
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-030]
---

## Summary

The sort chooser on the Issues tab and the four choosers in the new-issue form
are white boxes with white text. You have to open the dropdown to see what is
selected.

## Acceptance / done-when

- Every dropdown is readable closed, in both themes.

## Notes & worklog

- The text inputs beside them are fine, which is the clue: the problem is what
  paints a `<select>`, not the palette.

## Resolution

WebKitGTK paints a `<select>` as a native menulist and ignores the background
given to it, while still honouring the colour. So on the dark palette these
controls were near-white text on the native light widget. The `<input>` next to
each one looked right because WebKit does honour background there. Reproduced in
WebKitGTK rather than Chromium, which is the only place the difference shows.

`color-scheme` on `:root` was the first attempt and does not fix it: the
computed value took effect and the widget stayed light. The fix is to stop the
engine painting the control at all, with `appearance: none` and an explicit
background, colour, border and padding. That also removes the native arrow, so
one comes back as a background image. It is a palette entry, `--select-arrow`,
because a `data:` URI cannot refer to `currentColor`, and the chevron has to
change with the theme like everything else.

The rule is on the bare `select` element in `styles.css`, so it covers both
pages. That caught one the report did not mention: `#permission-mode-select` in
the transcript header had exactly the same bug.

Three rules had to give up the `background` shorthand for `background-color`,
since the shorthand resets `background-image` and would have erased the arrow:
`.issues-sort-select`, `.ff-select` (split out from `.ff-input`, which still
needs the full set) and `.add-section-row select`.

`color-scheme` stayed anyway. It is correct, it drives the scrollbars, and the
option popup is still drawn by the engine; `select option` is given an explicit
background and colour for the same reason.

Verified in WebKitGTK on the real pages in both themes: the Issues sorter, the
Status / Type / Area choosers in the new-issue form, the add-section chooser,
and the transcript's Mode dropdown.

### The hour this cost

The first fix appeared to do nothing: identical rendering, byte-identical
screenshots. The CSS was right and being served correctly. WebKitGTK keeps a
disk cache shared across processes, so every test run was loading the old
stylesheet. Evaluating `getComputedStyle` inside the page is what found it,
showing `appearance: auto` and `--select-arrow` empty against a file that
plainly set both.

Any WebKitGTK test of a stylesheet change has to use
`WebKit.NetworkSession.new_ephemeral()`, or it is testing the previous version.
The same trap is waiting for the app's own panes after a CSS edit.

### Noticed, not fixed

With all four schema sections already added, the add-section chooser renders as
an empty box with a chevron, because it has no options left to offer. It reads
as broken. Hiding the row when nothing is available is the fix; it predates this
issue and is not what was reported.
