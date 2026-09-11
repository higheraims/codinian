"""File operations for the project workspace track (M4).

`resolve_in_project` is the security boundary: every other function in this
module, and every filesystem-touching handler in `remote/server.py`, funnels
a client-supplied path through it before touching disk. The server can be
reachable over the tailnet (`docs/remote-access.md`), so a traversal bug here
is not a cosmetic bug, it is a read of `/etc/shadow` from someone else's
laptop. `Path.resolve(strict=True)` looks like it would do this job but
doesn't: it raises on a path that doesn't exist yet, which is exactly the
case of a file the caller is about to create, so the containment check below
resolves the parent instead and only requires the parent to already exist.

This module imports only the standard library, and does not import `vcs.py`
or anything else in the repo, so a caller can use it without pulling in git
or the rest of the app. The project root is always a `Path` passed in by the
caller; nothing here discovers or caches it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

MAX_TEXT_BYTES = 512 * 1024

# Read this many leading bytes to decide if a file is binary. Large enough to
# catch a NUL byte past a BOM or a short text preamble, small enough that the
# check costs nothing next to actually reading the file.
_BINARY_SNIFF_BYTES = 8000

# Fallback ignore set for a folder that isn't a git repo, where there is no
# `git check-ignore` to ask. Matched by directory/file name, not by pattern.
DEFAULT_IGNORED_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


class PathError(ValueError):
    """A client-supplied path failed the safety check. The server maps this
    to `400 bad_path` without inspecting the message, so the message here is
    for logs and the verification script, not for the client."""


class FileTooLarge(Exception):
    """Raised by `read_text_file` for a file over `MAX_TEXT_BYTES`. The
    server reads `.size` to answer `413`."""

    def __init__(self, size: int):
        self.size = size
        super().__init__(f"file too large: {size} bytes")


class BinaryFile(Exception):
    """Raised by `read_text_file` when a NUL byte turns up in the leading
    sniff window. The server reads `.size` to answer `415`."""

    def __init__(self, size: int):
        self.size = size
        super().__init__(f"binary file: {size} bytes")


def resolve_in_project(root: Path, rel: str) -> Path:
    """Resolve `rel` against `root` and return the absolute path, raising
    `PathError` for anything that isn't a plain descendant of `root`.

    Rejected: an empty path segment resolving outside the tree, an absolute
    path, a NUL byte, a backslash (Windows-style separators have no meaning
    here and existing only to be misread as one), anything that normalises
    to `.git` or under it, and anything that escapes `root` once `..` is
    resolved — including through a symlink that points outside the project,
    which is why this resolves symlinks itself rather than trusting
    `os.path.normpath` on the string alone.

    The final path component is allowed not to exist yet (creating a new
    file), but every component above it must exist and must itself resolve
    inside the project; a symlink partway up the chain that leads outside
    the root is still caught because `resolve()` follows it.
    """
    root = Path(root).resolve()

    if not isinstance(rel, str):
        raise PathError(f"path is not a string: {rel!r}")
    if "\x00" in rel:
        raise PathError("path contains a NUL byte")
    if "\\" in rel:
        raise PathError(f"path contains a backslash: {rel!r}")

    # PurePosixPath rejects nothing by itself (it happily represents "..");
    # it is only used here to detect an absolute-looking input uniformly,
    # since a bare `rel.startswith("/")` would miss "" and Path("").is_absolute
    # is platform-dependent for edge cases we'd rather not rely on.
    if rel.strip() == "":
        candidate_rel = PurePosixPath(".")
    else:
        candidate_rel = PurePosixPath(rel)
    if candidate_rel.is_absolute():
        raise PathError(f"absolute path rejected: {rel!r}")

    unresolved = root / rel

    # Resolve the parent directory chain, which must already exist, and
    # re-attach the final component unresolved-through-symlinks (it may not
    # exist, or may itself be a symlink the caller is about to replace).
    parent = unresolved.parent
    try:
        resolved_parent = parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathError(f"parent does not exist: {rel!r}") from exc

    resolved = resolved_parent / unresolved.name
    # If the final component itself exists, resolve through it too, so a
    # symlink sitting at the leaf (not just partway up) is caught.
    if resolved.is_symlink() or resolved.exists():
        try:
            resolved = resolved.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathError(f"path does not resolve: {rel!r}") from exc

    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathError(f"path escapes project root: {rel!r}") from None

    rel_parts = resolved.relative_to(root).parts
    if rel_parts and rel_parts[0] == ".git":
        raise PathError(f"path inside .git: {rel!r}")

    return resolved


def _entry_dict(root: Path, abs_path: Path, ignored: bool) -> dict | None:
    """One FileEntry, or None if `stat` fails (a broken symlink) — the
    caller drops those rather than letting one bad entry blow up a whole
    directory listing."""
    try:
        st = abs_path.stat()
    except OSError:
        return None
    return {
        "name": abs_path.name,
        "path": str(abs_path.relative_to(root)),
        "is_dir": abs_path.is_dir(),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "ignored": ignored,
    }


def list_dir(root: Path, rel: str, ignored_checker=None) -> list[dict]:
    """One directory level: FileEntry dicts, directories first then files,
    each case-insensitively alphabetical, hidden entries included.

    `ignored_checker`, when given, is a callable taking a list of
    root-relative paths and returning the set of ignored ones in one call
    (the server passes `vcs.check_ignore` for a repo, to keep git process
    spawns down to one per listing rather than one per entry). When it is
    None, an entry is "ignored" if its name is in `DEFAULT_IGNORED_NAMES`.
    """
    root = Path(root).resolve()
    dir_path = resolve_in_project(root, rel)
    if not dir_path.exists():
        raise FileNotFoundError(f"does not exist: {rel!r}")
    if not dir_path.is_dir():
        raise NotADirectoryError(f"not a directory: {rel!r}")

    try:
        children = list(dir_path.iterdir())
    except OSError:
        return []

    rel_paths = [str(c.relative_to(root)) for c in children]
    if ignored_checker is not None:
        ignored_set = ignored_checker(rel_paths)
        # `git check-ignore` never reports `.git` — it is excluded by git
        # itself rather than by an ignore rule — so without this the one
        # directory the client is categorically forbidden to open would be
        # the only one that did not look it.
        ignored_set |= {rp for rp, c in zip(rel_paths, children) if c.name == ".git"}
    else:
        ignored_set = {rp for rp, c in zip(rel_paths, children) if c.name in DEFAULT_IGNORED_NAMES}

    entries = []
    for child, rp in zip(children, rel_paths):
        entry = _entry_dict(root, child, rp in ignored_set)
        if entry is not None:
            entries.append(entry)

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].casefold()))
    return entries


def read_text_file(root: Path, rel: str) -> dict:
    """`{"path", "content", "size", "mtime"}` for a text file. Raises
    `FileNotFoundError` when nothing is there and `IsADirectoryError` when
    `rel` names a directory — distinct from `PathError`, since "outside the
    project" and "not there" call for different responses. Raises
    `FileTooLarge` above `MAX_TEXT_BYTES` and `BinaryFile` when a NUL byte
    shows up in the first `_BINARY_SNIFF_BYTES` bytes — checked in that
    order, on the raw bytes, before any text decoding is attempted, so a
    huge binary file doesn't get read twice."""
    root = Path(root).resolve()
    path = resolve_in_project(root, rel)
    if not path.exists():
        raise FileNotFoundError(f"does not exist: {rel!r}")
    if path.is_dir():
        raise IsADirectoryError(f"is a directory: {rel!r}")

    st = path.stat()
    if st.st_size > MAX_TEXT_BYTES:
        raise FileTooLarge(st.st_size)

    raw = path.read_bytes()
    if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
        raise BinaryFile(st.st_size)

    return {
        "path": str(path.relative_to(root)),
        "content": raw.decode("utf-8", errors="replace"),
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def write_text_file(root: Path, rel: str, content: str) -> dict:
    """Write `content`, through a temp file in the same directory then a
    rename, so a failed or interrupted write cannot truncate the original
    (same pattern as `config.py` and `project.py`). Creates the file if
    absent; does not create parent directories, matching the contract."""
    root = Path(root).resolve()
    path = resolve_in_project(root, rel)
    if not path.parent.is_dir():
        raise FileNotFoundError(f"parent directory does not exist: {rel!r}")

    data = content.encode("utf-8")
    tmp = path.with_name(path.name + ".codinian-tmp")
    tmp.write_bytes(data)
    tmp.replace(path)

    st = path.stat()
    return {"path": str(path.relative_to(root)), "size": st.st_size, "mtime": st.st_mtime}


def create_entry(root: Path, rel: str, kind: str) -> dict:
    """Create an empty file or a directory at `rel`. Raises `FileExistsError`
    if anything is already there — the server maps that to `409 exists` —
    and `ValueError` for a `kind` other than "file" or "dir"."""
    if kind not in ("file", "dir"):
        raise ValueError(f"invalid kind: {kind!r}")

    root = Path(root).resolve()
    path = resolve_in_project(root, rel)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"already exists: {rel!r}")
    if not path.parent.is_dir():
        raise FileNotFoundError(f"parent directory does not exist: {rel!r}")

    if kind == "dir":
        path.mkdir()
    else:
        path.touch()

    return {"path": str(path.relative_to(root))}


def rename_entry(root: Path, rel: str, to: str) -> dict:
    """Rename/move `rel` to `to`, both root-relative. Raises
    `FileExistsError` if the destination is already taken — the server maps
    that to `409 exists` — and `FileNotFoundError` if the source is missing."""
    root = Path(root).resolve()
    src = resolve_in_project(root, rel)
    dst = resolve_in_project(root, to)

    if not (src.exists() or src.is_symlink()):
        raise FileNotFoundError(f"source does not exist: {rel!r}")
    if dst.exists() or dst.is_symlink():
        raise FileExistsError(f"destination already exists: {to!r}")
    if not dst.parent.is_dir():
        raise FileNotFoundError(f"destination parent does not exist: {to!r}")

    src.rename(dst)
    return {"path": str(dst.relative_to(root))}


def open_external(root: Path, rel: str) -> None:
    """Launch `xdg-open` on the resolved path, detached from this process:
    stdio to devnull and a new session, so the child outlives the server
    (and its exit doesn't turn into a zombie or, worse, a wait that blocks a
    request handler) and produces nothing on the server's own stdout."""
    root = Path(root).resolve()
    path = resolve_in_project(root, rel)
    if not path.exists():
        raise FileNotFoundError(f"does not exist: {rel!r}")

    devnull = subprocess.DEVNULL
    subprocess.Popen(
        ["xdg-open", str(path)],
        stdin=devnull,
        stdout=devnull,
        stderr=devnull,
        start_new_session=True,
    )
