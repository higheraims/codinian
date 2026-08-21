"""Session templates and per-folder defaults (ISSUE-014).

Two related but distinct things live in one file here:

- Named templates: a name plus the settings a new session starts with
  (working directory, permission mode). Created and picked explicitly,
  from the new-session dialog's template chooser.
- Per-working-directory defaults: the last-used permission mode for a
  folder, recorded automatically every time a session starts there. This
  is what covers a folder that is *not* a registered project -- the gap
  `project.settings_override` cannot close, since that function only
  knows about `.codinian/settings.json` inside a registered repo.

One file rather than two: both are small, written rarely (a save, or once
per session start), and read together on every new-session dialog open.
Splitting them would mean two atomic-write paths and two env-var
overrides for no benefit, since nothing here ever writes one half without
the process already having read both.

Templates and folder defaults are stored where the session DB already
lives (`~/.local/share/codinian/`), not in the repo -- unlike
`project.py`'s `.codinian/settings.json`, this is per-machine state, with
nothing here for another contributor to read or diff.

A session never stores which template it came from: picking a template
only pre-fills the dialog's fields at that moment, and nothing keeps a
reference afterwards. Deleting a template later cannot affect a session
already running or already recorded in the database.

Kept free of `gi` imports, like `project.py` and `agent_options.py`, so
`resolve_permission_mode` below is checkable without a display.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import agent_options
import project
import session

# `CODINIAN_TEMPLATES_PATH` points this module at a different file, the same
# way `CODINIAN_CONFIG` does for config.py. Test runs use it to keep from
# ever touching the real store; ordinary runs never set it.
TEMPLATES_PATH = Path(os.environ.get("CODINIAN_TEMPLATES_PATH") or
                      Path.home() / ".local/share/codinian/templates.json")

VERSION = 1

def _empty() -> dict:
    return {"version": VERSION, "templates": [], "folder_defaults": {}}


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically: a temp file, then a rename, so a crash
    mid-write cannot leave a half-written store. Mirrors
    `project._atomic_write_json`; kept local rather than imported so this
    module's on-disk format stays independent of the registry's."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def _clean_template(entry) -> dict | None:
    """A well-formed template dict from a raw JSON entry, or None if the
    entry is too malformed to use (missing or empty name)."""
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        return None
    mode = entry.get("permission_mode")
    if mode not in session.PERMISSION_MODES:
        mode = "default"
    return {
        "name": name,
        "workdir": entry.get("workdir") if isinstance(entry.get("workdir"), str) else "",
        "permission_mode": mode,
    }


def _load() -> dict:
    """Read the store. A missing file, one that fails to parse as JSON, or
    one that is not the expected shape degrades to empty rather than
    raising -- the same rule `project.load_settings` applies to a
    hand-edited settings file. Individual malformed template entries are
    dropped rather than failing the whole load."""
    if not TEMPLATES_PATH.exists():
        return _empty()
    try:
        data = json.loads(TEMPLATES_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()

    raw_templates = data.get("templates")
    templates = []
    if isinstance(raw_templates, list):
        for entry in raw_templates:
            cleaned = _clean_template(entry)
            if cleaned is not None:
                templates.append(cleaned)

    raw_defaults = data.get("folder_defaults")
    folder_defaults = {}
    if isinstance(raw_defaults, dict):
        for key, value in raw_defaults.items():
            if isinstance(key, str) and value in session.PERMISSION_MODES:
                folder_defaults[key] = value

    return {"version": VERSION, "templates": templates, "folder_defaults": folder_defaults}


def _save(data: dict) -> None:
    _atomic_write_json(TEMPLATES_PATH, data)


# --- Named templates --------------------------------------------------

def list_templates() -> list[dict]:
    """All saved templates, each a dict with name/workdir/permission_mode,
    in the order they were created."""
    return _load()["templates"]


def get_template(name: str) -> dict | None:
    """One template by name, or None if there is no such template."""
    for t in list_templates():
        if t["name"] == name:
            return dict(t)
    return None


def save_template(name: str, *, workdir: str = "",
                  permission_mode: str = "default") -> dict:
    """Save a template, overwriting any existing template of the same
    name. Returns the template as saved."""
    name = name.strip()
    if not name:
        raise ValueError("template name must not be empty")
    if permission_mode not in session.PERMISSION_MODES:
        permission_mode = "default"

    data = _load()
    entry = {"name": name, "workdir": workdir, "permission_mode": permission_mode}
    data["templates"] = [t for t in data["templates"] if t["name"] != name] + [entry]
    _save(data)
    return entry


def delete_template(name: str) -> bool:
    """Remove a template by name. Returns whether one was actually
    removed.

    Safe to call at any time: a session never stores a reference to the
    template it started from (see the module docstring), so this cannot
    affect a session that is already running."""
    data = _load()
    remaining = [t for t in data["templates"] if t["name"] != name]
    if len(remaining) == len(data["templates"]):
        return False
    data["templates"] = remaining
    _save(data)
    return True


# --- Per-working-directory defaults ------------------------------------

def record_folder_mode(workdir: str, permission_mode: str) -> None:
    """Remember `permission_mode` as the last-used mode for `workdir`,
    keyed on the resolved absolute path so a relative or symlinked
    spelling of the same folder still hits the same entry. Silently does
    nothing for a mode outside `session.PERMISSION_MODES`, the same
    validation `_load` applies on read."""
    if permission_mode not in session.PERMISSION_MODES:
        return
    resolved = str(Path(workdir).resolve())
    data = _load()
    data["folder_defaults"][resolved] = permission_mode
    _save(data)


def folder_mode(workdir: str) -> str | None:
    """The last-used permission mode recorded for `workdir`, or None if
    none is recorded yet."""
    resolved = str(Path(workdir).resolve())
    return _load()["folder_defaults"].get(resolved)


# --- Precedence ----------------------------------------------------------

def resolve_permission_mode(*, config: dict, workdir: str, template: dict | None = None,
                            prior_mode: str | None = None) -> str:
    """The permission mode a new-session dialog should preselect, in
    order:

    0. when resuming, the mode that conversation was last running under, from
       its own transcript. Resuming continues a conversation, so it continues
       under the terms the conversation was already on; falling through to a
       project default is what brought an unattended session back asking to
       approve every command.
    1. the chosen template's mode, if a template was picked
    2. else the project's own `.codinian/settings.json` mode, via
       `project.settings_override` (not `project.load_settings`, which
       merges in a default and would mask "this repo never said")
    3. else the remembered last-used mode for this exact working
       directory
    4. else the global fallback, `agent_options.default_permission_mode`

    A single function so the precedence is one thing to test and one
    thing to change, rather than logic repeated across UI callbacks.
    """
    if prior_mode in session.PERMISSION_MODES:
        return prior_mode

    if template is not None:
        mode = template.get("permission_mode")
        if mode in session.PERMISSION_MODES:
            return mode

    project_mode = project.settings_override(workdir, "default_permission_mode")
    if project_mode in session.PERMISSION_MODES:
        return project_mode

    remembered = folder_mode(workdir)
    if remembered in session.PERMISSION_MODES:
        return remembered

    return agent_options.default_permission_mode(config)
