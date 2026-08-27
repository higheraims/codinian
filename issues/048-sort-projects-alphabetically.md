---
id: ISSUE-048
title: Projects do not sort in the main view
status: done
type: feature
area: gui
created: 2026-08-27
updated: 2026-08-27
related: [ISSUE-020]
---

## Summary

Projects appear in the order they were registered and never move. `add_project`
appends to `~/.local/share/codinian/projects.json` and nothing sorted
afterwards: not the GTK sidebar, not `GET /api/projects`, not the new-session
dialog's project combo. Files, issues, sessions and slash commands all sorted;
projects were the omission.

Registration order carries no meaning. It records the afternoon someone happened
to add a folder, so a project's position in the list is unguessable and the list
gets harder to scan as it grows.

## Acceptance / done-when

- The sidebar, the browser's project cards and the new-session dialog all show
  projects in the same order.
- A project added while the app is running lands in position, not at the bottom.
- Picking a project in the new-session dialog still starts the session in that
  project's folder.

## Notes & worklog

- Sorted in `load_registry()`, which is the one place all three consumers pass
  through, rather than in each renderer. The browser needed no change: it
  renders the API's array as received.
- The GTK sidebar needed a second change. `_add_project_row` can only append, so
  a project added at runtime would have sat at the bottom until restart;
  `Gtk.ListBox.set_sort_func` fixes that without rebuilding rows that carry
  popovers and gesture controllers.
- `sort_key` takes name and path as two arguments rather than a `Project`, so
  the sidebar can call it with the ProjectMeta dict its rows carry.

## Resolution

Projects sort by name, case-insensitively, with the path breaking ties so two
folders of the same name in different parents keep a stable order between runs.
Alphabetical because it is the order a user can predict without being told the
rule, which is what matters for a list you scan rather than read.

Boundaries, chosen rather than missed:

- **No sort control and no stored sort preference.** One order, the same
  everywhere, nothing to configure. The issues tab has a sort `<select>` if a
  second order is ever wanted, but a second order needs a reason first.
- **No recency sort.** `added_at` is the only timestamp on a project and it is
  never refreshed, since `add_project` returns early for an already-registered
  folder. It means "when this was registered", not "when this was last touched".
  A real recency sort needs a persisted `last_opened`, which is a registry
  schema change in four places plus a disk write on every project open, and that
  write would bump `_registry_generation` and flush `cached_resolver` each time.
- **Plain lexicographic, not natural sort.** `web10` sorts before `web2`. The
  web client has a numeric-aware comparator in the command palette, but using it
  here would mean sorting client-side, putting the rule in two languages and
  three places. Python's equivalent is a digit-run tokeniser, which is real code
  for a case that does not arise in folder names.
- **Missing projects stay in place.** A folder whose drive is unmounted keeps
  its alphabetical position and its "Missing" subtitle rather than sinking to the
  bottom, so a project does not move when its availability changes.
