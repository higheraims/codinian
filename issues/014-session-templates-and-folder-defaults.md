---
id: ISSUE-014
title: Session templates and per-folder defaults
status: done
type: feature
area: gui
created: 2026-08-16
updated: 2026-08-20
related: [ISSUE-012, ISSUE-018, ISSUE-020]
---

## Summary

Starting a session in a familiar project means re-picking the same directory, the same
permission mode, and the same MCP configuration every time. Store defaults per working
directory and offer named templates in the new-session dialog, so "start a session in
that project" is one click with the right settings already applied.

Backlog item from the M4 list in the pivot plan. It becomes more valuable as the number of
per-session settings grows: permission mode ([[ISSUE-012]]), MCP servers, and any tools
made available to sessions ([[ISSUE-018]]).

## Acceptance / done-when

- A working directory remembers its last-used permission mode and MCP config.
  (Permission mode: stored by `templates.record_folder_mode`. MCP config: the
  CLI already keys it to the working directory and Codinian does not interfere
  -- see the 2026-08-20 MCP worklog entry.)
- Named templates can be created, picked in the new-session dialog, and edited.
- Removing a template does not break sessions started from it.

## Notes & worklog

- Templates and folder defaults are stored where the session DB already lives
  (`~/.local/share/codinian/`), not in the repo.

- **2026-08-20, partly delivered by M4.** The per-repo settings file this issue
  needed now exists: `.codinian/settings.json` holds `default_permission_mode`,
  and the new-session dialog offers a project chooser that fills in the working
  directory and preselects that mode. Picking a project is the one-click case
  this issue opens with.

  What is still missing is the rest of it: named templates, MCP configuration,
  and defaults keyed to a working directory that is not a registered project.
  Staying open for those.

- **2026-08-20, named templates and per-folder defaults.** New module
  `templates.py` owns both, storing them at `~/.local/share/codinian/templates.json`
  (overridable with `CODINIAN_TEMPLATES_PATH`, the same pattern `config.py`
  uses for `CODINIAN_CONFIG`). One file, not two: a template and a folder
  default are both small, rarely written, and read together every time the
  new-session dialog opens, so a second file would mean a second atomic-write
  path and a second env var for nothing gained. A corrupt or hand-edited file
  degrades to no templates and no folder defaults, matching `project.load_settings`'s
  rule for a bad settings file, and individual malformed template entries are
  dropped without failing the rest of the load.

  The precedence the dialog now applies to preselect a permission mode lives
  in one function, `templates.resolve_permission_mode(config, workdir, template)`:
  a picked template's mode, else the project's own `.codinian/settings.json`
  (via `project.settings_override`, never `load_settings`), else the
  remembered last-used mode for that exact working directory, else the
  global `default_permission_mode`. `session_dialog.py` calls it from one
  place (`_apply_permission_precedence`) rather than repeating the chain in
  each callback that can change the working directory or the picked template.

  `session_dialog.py` gained a Template group: an `Adw.ComboRow` listing
  saved templates with "None" first, a delete button on that row, and a
  "Save as Template" entry with its own save button. Picking a template
  fills in working directory, permission mode, and goal, and is then
  forgotten -- the `Session` dataclass has no field that names a template,
  so deleting one afterward cannot touch a session already created from it.
  Creating a session now also calls `templates.record_folder_mode`, so the
  working directory's mode is remembered even when that directory is not a
  registered project.

  Verified with two headless scripts (no GTK: `templates.py`'s storage and
  the four-level precedence: round-trip, overwrite, delete, corrupt-file
  degradation, and each precedence level winning in turn including an
  invalid template mode falling through; with GTK, `Adw.init()` plus a
  `NewSessionDialog` built but never presented: the template chooser exists
  and lists saved templates, picking one changes the preselected permission
  mode and fills in workdir/goal, deleting the picked template re-resolves
  the mode through the remaining precedence rather than leaving the deleted
  template's value in place, and `_on_create` records the folder's mode).
  Both scripts point `CODINIAN_TEMPLATES_PATH` and `CODINIAN_CONFIG` at a
  scratch directory; the real templates store was never created and the
  real config file was never written to.

  **Since closed:** the dialog was then built from a real `CodinianWindow`,
  the same construction path `window.py` uses, and asserted on the widgets the
  dialog holds rather than by walking its tree (an `Adw.Dialog`'s children are
  not reachable before it is presented, which is what made the first attempt
  at this test report no widgets at all). Confirmed there: the Template
  chooser lists a saved template with "None" first, picking it moves the
  permission chooser to that template's mode, and a template deleted between
  two dialogs is absent from the second while the second still resolves a
  mode.

  **Also since closed:** the Template group was then looked at in a real
  window, presented from a real `CodinianWindow`. It renders as its own
  section below Session Details, with the chooser and its delete button on one
  row and the name entry and its save button on the next, matching the rows
  above it. The screenshot did turn up a rendering fault, but not in this
  group and not in this project's code: every `Adw.EntryRow` in the app draws
  a broken-image square. Filed as [[ISSUE-034]].
  `project.py`'s project registry has no env-var override, so the GTK test's
  call into `project.load_registry()` did read this machine's real,
  already-registered projects; that call is read-only and nothing in the
  test depended on what it returned, but it means the test did not run
  fully sandboxed from machine state.

- **2026-08-20, the MCP half of the acceptance list: already true, and worth
  keeping that way.** The first pass here recorded MCP configuration as
  undelivered, on the reasoning that nothing in Codinian gives a session an MCP
  config. That was the wrong place to look. `ClaudeAgentOptions.strict_mcp_config`
  defaults to `False`, and the SDK defines that as loading the project's
  `.mcp.json`, the user and global settings, and plugin-provided servers.
  Codinian sets neither `mcp_servers` nor `strict_mcp_config`, so a session
  started in a folder already picks up that folder's MCP configuration exactly
  as the CLI would -- which is the per-working-directory behaviour this issue
  asked for, keyed by the file the user already edits.

  So the work is not to add a store; it is to not break the one that exists.
  Setting `mcp_servers` alone would add to the discovered set, but
  `strict_mcp_config=True` would silently drop every server the user configured
  outside Codinian, and the failure would look like an MCP tool simply going
  missing. `agent_options.options_kwargs` now carries a comment saying so, and
  `test_agent_options.py` asserts across every combination of thinking and
  effort settings that neither key is ever produced.
