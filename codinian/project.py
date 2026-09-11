"""Projects and per-repo settings for the workspace track (M4).

A project is a folder the user has registered on this machine. The registry
itself — which folders are known, and under what id — is per-machine state,
so it lives in `~/.local/share/codinian/projects.json` next to the rest of
Codinian's local data, not in the repo. A project's settings, by contrast, are
meant to travel with the code (issue tags, the issues directory, the default
permission mode are a property of the repo, not of whoever happens to be
running the server), so those live in `.codinian/settings.json` inside the
project and are meant to be committed.

This module owns both files and nothing else: no session state, no issue
parsing, no git calls. It imports only the standard library so that every
other module in this project can import it without pulling in the rest of
the app.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REGISTRY_PATH = Path.home() / ".local/share/codinian/projects.json"

# Settings have lived under three directory names, one per rename of the app.
# Reads walk this tuple newest first, so a repo that was never migrated keeps
# working instead of silently falling back to DEFAULT_SETTINGS; writes always
# land in the current one, so a repo migrates the first time a setting changes.
SETTINGS_DIRS = (".codinian", ".codigulus", ".claudius")
SETTINGS_RELPATH = f"{SETTINGS_DIRS[0]}/settings.json"


def settings_path(root: str | Path) -> Path:
    """The settings file to read for `root`: the first that exists among the
    current and former directory names, else the current one. Returns a path
    that may not exist; callers already handle a missing file."""
    base = Path(root)
    for name in SETTINGS_DIRS:
        candidate = base / name / "settings.json"
        if candidate.exists():
            return candidate
    return base / SETTINGS_RELPATH

REGISTRY_VERSION = 1
SETTINGS_VERSION = 1

DEFAULT_SETTINGS = {
    "version": SETTINGS_VERSION,
    "name": "",
    "default_permission_mode": "default",
    "issues": {
        "dir": "",
        "id_prefix": "ISSUE",
        "statuses": ["open", "in-progress", "blocked", "done", "dropped", "superseded"],
        "types": ["feature", "bug", "chore", "spec", "spike"],
        "areas": ["gui", "sdk", "remote", "db", "tools", "docs", "packaging"],
        "sections": ["Summary", "Acceptance / done-when", "Notes & worklog", "Resolution"],
    },
}

# The key order settings are written in, so a diff only ever touches the
# fields that actually changed rather than reshuffling the whole file.
_SETTINGS_KEY_ORDER = ["version", "name", "default_permission_mode", "issues"]

# Incremented on every registry write in this process; see registry_generation.
_registry_generation = 0
_ISSUES_KEY_ORDER = ["dir", "id_prefix", "statuses", "types", "areas", "sections"]


def project_id(path: str | Path) -> str:
    """The stable id for a project path: the first 8 hex characters of the
    SHA-256 of the resolved absolute path. Stable across restarts, and the
    same folder (however it is spelled — a trailing slash, a symlink, `..`)
    always yields the same id."""
    resolved = str(Path(path).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:8]


@dataclass
class Project:
    id: str
    path: str
    name: str
    added_at: str  # ISO 8601

    def meta(self) -> dict:
        """ProjectMeta per the contract doc, computed fresh from disk each
        call rather than cached, since `exists` and `is_repo` can change
        under us at any time."""
        root = Path(self.path)
        exists = root.is_dir()
        # A worktree or a submodule has a .git *file* (pointing elsewhere),
        # not a directory, so either counts as "is a repo" here.
        is_repo = exists and (root / ".git").exists()
        return {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "added_at": self.added_at,
            "exists": exists,
            "is_repo": is_repo,
            "issues_dir": resolve_issues_dir(self.path) if exists else "issues",
        }


def sort_key(name: str, path: str) -> tuple[str, str]:
    """The order projects are shown in, everywhere: by name, case-insensitively.

    Registration order is what the registry file happens to hold, and it says
    nothing a user could predict, so every surface sorts by this instead
    (ISSUE-048). The path breaks ties, so two folders of the same name in
    different parents keep a stable order between runs rather than swapping
    places on a whim. `casefold` rather than `lower`, matching the directory
    listing in files.py.

    Takes the two fields rather than a Project so the GTK sidebar can call it
    with a ProjectMeta dict, which is all its rows carry, without either side
    restating the rule.
    """
    return (name.casefold(), path)


def _atomic_write_json(path: Path, data: dict, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(path)


def load_registry() -> list[Project]:
    """Read the registry. A missing or corrupt file, or one that fails to
    parse as JSON, yields an empty list rather than raising — the registry is
    rebuilt by re-adding projects, so failing startup over it would be worse
    than losing it. Individual malformed entries are dropped rather than
    failing the whole load.

    Sorted by `sort_key`. This is the one place every consumer passes through
    (the HTTP list endpoint, the GTK sidebar at startup, the new-session
    dialog), so sorting here is what gives all three the same order without
    each one restating the rule."""
    if not REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    raw_projects = data.get("projects")
    if not isinstance(raw_projects, list):
        return []

    projects = []
    for entry in raw_projects:
        if not isinstance(entry, dict):
            continue
        try:
            projects.append(
                Project(
                    id=str(entry["id"]),
                    path=str(entry["path"]),
                    name=str(entry["name"]),
                    added_at=str(entry["added_at"]),
                )
            )
        except KeyError:
            continue
    return sorted(projects, key=lambda p: sort_key(p.name, p.path))


def registry_generation() -> int:
    """Bumped by every registry write in this process. Lets a cache tell a
    change it caused itself apart from one it merely has not noticed yet."""
    return _registry_generation


def save_registry(projects: list[Project]) -> None:
    """Write the registry, atomically, in the shape documented in
    docs/project-workspace-protocol.md."""
    global _registry_generation
    _registry_generation += 1
    data = {
        "version": REGISTRY_VERSION,
        "projects": [
            {"id": p.id, "path": p.path, "name": p.name, "added_at": p.added_at}
            for p in projects
        ],
    }
    _atomic_write_json(REGISTRY_PATH, data)


def add_project(path: str) -> Project:
    """Register a folder as a project, idempotently: an already-registered
    folder (matched by resolved path, so a trailing slash or a symlink to it
    both hit the same entry) returns the existing entry rather than a
    duplicate. Raises FileNotFoundError if `path` is not an existing
    directory."""
    resolved = Path(path).resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"not a directory: {path}")

    pid = project_id(resolved)
    projects = load_registry()
    for p in projects:
        if p.id == pid:
            return p

    from datetime import datetime, timezone

    new_project = Project(
        id=pid,
        path=str(resolved),
        name=resolved.name,
        added_at=datetime.now(timezone.utc).isoformat(),
    )
    projects.append(new_project)
    save_registry(projects)
    return new_project


def remove_project(pid: str) -> bool:
    """Drop a project from the registry. Never touches anything on disk
    inside the project itself. Returns whether an entry was actually
    removed."""
    projects = load_registry()
    remaining = [p for p in projects if p.id != pid]
    if len(remaining) == len(projects):
        return False
    save_registry(remaining)
    return True


def get_project(pid: str) -> Project | None:
    for p in load_registry():
        if p.id == pid:
            return p
    return None


def find_project_for_path(path: str) -> Project | None:
    """The registered project whose path is the longest prefix of `path`,
    matched on resolved path *components* so that e.g. `/home/x/proj` does
    not claim `/home/x/project-other` just because the strings share a
    prefix."""
    target = Path(path).resolve()
    target_parts = target.parts

    best: Project | None = None
    best_len = -1
    for p in load_registry():
        proj_parts = Path(p.path).resolve().parts
        n = len(proj_parts)
        if target_parts[:n] == proj_parts and n > best_len:
            best = p
            best_len = n
    return best


def _deep_merge(defaults: dict, override: dict) -> dict:
    """Merge `override` onto `defaults`, recursively for nested dicts, so a
    settings file that only sets `issues.areas` still gets every other
    `issues` key from the defaults instead of losing them."""
    result = dict(defaults)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(root: str | Path) -> dict:
    """The effective settings for a project: DEFAULT_SETTINGS deep-merged
    under whatever `settings_path` resolves to, so a repo still on
    `.codigulus/` or `.claudius/` is read rather than ignored. A missing or
    corrupt file yields the defaults untouched."""
    path = settings_path(root)
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULT_SETTINGS))

    return _deep_merge(DEFAULT_SETTINGS, data)


def settings_override(root: str | Path, key: str):
    """What the project's settings file actually says for `key`, or None
    when the file does not mention it. The file is whichever of the current
    and former directory names `settings_path` finds.

    `load_settings` merges DEFAULT_SETTINGS, so every key is always present and
    a repo that never expressed a preference is indistinguishable from one that
    chose the default. That distinction matters where a global setting is the
    fallback: without this, a project with no `default_permission_mode` of its
    own would override the user's global choice with the default (ISSUE-030).
    """
    path = settings_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get(key)


def _ordered(d: dict, order: list[str]) -> dict:
    """`d`'s items in `order`, with any keys not in `order` appended after,
    in their existing order — so an unrecognised key is preserved rather
    than silently dropped."""
    result = {}
    for key in order:
        if key in d:
            result[key] = d[key]
    for key, value in d.items():
        if key not in result:
            result[key] = value
    return result


def _is_contained_relative(rel: str) -> bool:
    """True when `rel` is a plain relative path that stays inside its root.

    Pure string/path math, no filesystem: rejects absolute paths, NUL bytes,
    backslashes, the empty string, and any `..` that would climb above the
    root. The issues directory is the one project-relative path that does not
    pass through `files.resolve_in_project` (issues.py joins it directly), and
    it comes from a settings file a client can write, so it is validated here
    instead. Without this, `issues.dir` of `/etc` or `../../elsewhere` would
    let a token holder read and write `NNN-*.md` files anywhere the process can.
    """
    if not isinstance(rel, str) or rel.strip() == "" or "\x00" in rel or "\\" in rel:
        return False
    pure = PurePosixPath(rel)
    if pure.is_absolute():
        return False
    depth = 0
    for part in pure.parts:
        if part == "..":
            depth -= 1
            if depth < 0:
                return False
        elif part != ".":
            depth += 1
    return True


def save_settings(root: str | Path, settings: dict) -> dict:
    """Write `<root>/.codinian/settings.json`, creating `.codinian/` if
    needed, atomically, 2-space indent, trailing newline, stable key order.
    `settings` is deep-merged onto DEFAULT_SETTINGS first (the same rule
    load_settings applies), so a partial write — e.g. just a changed
    `issues.areas` — still lands on disk as a complete, canonical Settings
    object rather than a fragment missing every key the caller didn't
    mention. These files are meant to be committed to the user's repo, so
    they get ordinary permissions, not the 0600 that config.py uses for the
    local token file. Returns the settings as now on disk."""
    path = Path(root) / SETTINGS_RELPATH

    merged = _deep_merge(DEFAULT_SETTINGS, settings)
    configured = merged.get("issues", {}).get("dir")
    if configured and not _is_contained_relative(configured):
        raise ValueError(f"issues.dir must stay inside the project: {configured!r}")
    ordered = _ordered(merged, _SETTINGS_KEY_ORDER)
    if isinstance(ordered.get("issues"), dict):
        ordered["issues"] = _ordered(ordered["issues"], _ISSUES_KEY_ORDER)

    _atomic_write_json(path, ordered)
    return ordered


def resolve_issues_dir(root: str | Path, settings: dict | None = None) -> str:
    """The root-relative issues directory, per the three-step discovery in
    docs/project-workspace-protocol.md: the settings value if present, else
    the first of `issues/` or `docs/issues/` that already exists on disk,
    else `issues/`. Never creates anything."""
    if settings is None:
        settings = load_settings(root)

    # A settings file can be written through the API or edited by hand, so the
    # configured value is not trusted to stay inside the project; a bad one is
    # ignored in favour of the default rather than followed out of the tree.
    configured = settings.get("issues", {}).get("dir")
    if configured and _is_contained_relative(configured):
        return configured

    root_path = Path(root)
    if (root_path / "issues").is_dir():
        return "issues"
    if (root_path / "docs" / "issues").is_dir():
        return "docs/issues"
    return "issues"


def cached_resolver(ttl_seconds: float = 2.0):
    """A `workdir -> project id or None` lookup, for Session.meta().

    Every session's metadata carries its project, and the session list is
    rebroadcast on every status and usage event, so this is called far more
    often than the registry changes. The cache keeps a hot path off the
    filesystem without ever showing a stale answer for a change this process
    made: a registry write bumps a generation counter the cache checks, so
    adding a project claims its sessions on the very next call. The time-based
    expiry is only there for a change made by some other process.
    """
    import time

    state = {"at": 0.0, "gen": -1, "roots": []}

    def resolve(workdir: str) -> str | None:
        now = time.monotonic()
        gen = registry_generation()
        if gen != state["gen"] or now - state["at"] > ttl_seconds:
            state["roots"] = [(Path(p.path).resolve().parts, p.id)
                              for p in load_registry()]
            state["at"] = now
            state["gen"] = gen
        try:
            target = Path(workdir).resolve().parts
        except OSError:
            return None
        best_id, best_len = None, -1
        for parts, pid in state["roots"]:
            n = len(parts)
            if target[:n] == parts and n > best_len:
                best_id, best_len = pid, n
        return best_id

    return resolve
