"""Git operations for the project workspace track (M4).

Every call here shells out to the real `git` binary rather than reimplementing
any of it, on the theory that a hand-rolled porcelain-v1 parser is more
trustworthy than a hand-rolled index reader. Every subprocess call goes
through `_git`, which fixes the invocation shape once: no shell, an explicit
working directory via `-C` rather than `os.chdir` (so this module is safe to
call from a server handling concurrent requests against different projects),
a timeout, and `check=False` so a failing git call becomes a `GitError` this
module's caller can catch, not an uncaught `CalledProcessError`.

This module only ever adds to a repo's history or reads it. It does not
rewrite history, discard working-tree changes, or talk to a remote — no
`reset --hard`, `checkout --`, `clean`, `push`, `pull`, `fetch`, `rebase`,
`commit --amend`, or `tag -d`/`-f` anywhere below. An operation that can lose
committed or uncommitted work needs its own issue and its own confirmation
flow (`docs/project-workspace-protocol.md`), not a quiet addition here.

Imports only the standard library, and does not import `files.py` or
anything else in the repo, so a caller can use it without pulling in the
rest of the app. The project root is always a `Path` passed in by the
caller; nothing here discovers or caches it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

GIT_TIMEOUT = 15

# Field separator for multi-value `git log --format`, chosen because it
# cannot appear in a commit subject the way a comma or pipe plausibly could.
_FIELD_SEP = "\x1f"


class GitError(RuntimeError):
    """A git invocation exited non-zero. Carries the pieces a caller needs
    to report `422 git_failed` with git's own explanation in `detail`."""

    def __init__(self, message: str, *, returncode: int, stderr: str):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(message)


def _git(root: Path, *args: str, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=False,
        input=input,
    )


def is_repo(root: Path) -> bool:
    """Whether `root` itself is a repo root: it has a `.git` entry directly
    inside it. A directory (the ordinary case) or a file (a worktree or
    submodule pointer) both count, matching `project.py`'s own check. This
    is deliberately not "is `root` somewhere inside a repo" — a project
    registered as a subfolder of someone's larger checkout should read as
    not-a-repo rather than silently adopting the ancestor's history."""
    return (Path(root) / ".git").exists()


def _branch_info(root: Path) -> tuple[str | None, bool]:
    result = _git(root, "symbolic-ref", "--short", "HEAD")
    if result.returncode == 0:
        return result.stdout.strip(), False
    # No symbolic ref means detached HEAD — but only meaningful once a
    # commit exists to detach onto; on a brand-new repo HEAD is always a
    # symbolic ref to an unborn branch, so this branch is rare in practice.
    result = _git(root, "rev-parse", "--short", "HEAD")
    if result.returncode == 0:
        return result.stdout.strip(), True
    return None, False


def _upstream_info(root: Path) -> tuple[str | None, int, int]:
    result = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if result.returncode != 0:
        return None, 0, 0
    upstream = result.stdout.strip()

    counts = _git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if counts.returncode != 0:
        return upstream, 0, 0
    parts = counts.stdout.split()
    if len(parts) != 2:
        return upstream, 0, 0
    behind, ahead = int(parts[0]), int(parts[1])
    return upstream, ahead, behind


def _last_commit(root: Path) -> dict | None:
    result = _git(root, "log", "-1", f"--format=%H{_FIELD_SEP}%s{_FIELD_SEP}%cI")
    if result.returncode != 0:
        return None  # no commits yet
    parts = result.stdout.strip("\n").split(_FIELD_SEP)
    if len(parts) != 3:
        return None
    commit_hash, subject, date = parts
    return {"hash": commit_hash, "subject": subject, "date": date}


def _user_info(root: Path) -> dict | None:
    """The repo's effective `user.name`/`user.email` (falling through
    global/system config the same way a real `git commit` would), or None
    when either is unset — the client's signal for whether to offer Commit
    at all."""
    name = _git(root, "config", "--get", "user.name")
    email = _git(root, "config", "--get", "user.email")
    if name.returncode != 0 or email.returncode != 0:
        return None
    name_value, email_value = name.stdout.strip(), email.stdout.strip()
    if not name_value or not email_value:
        return None
    return {"name": name_value, "email": email_value}


def _changes(root: Path) -> list[dict]:
    result = _git(root, "status", "--porcelain=v1", "-z")
    if result.returncode != 0:
        raise GitError("git status failed", returncode=result.returncode, stderr=result.stderr)

    # -z terminates every record with NUL, so splitting on it leaves one
    # trailing empty string for a clean tree, or after the last record.
    tokens = result.stdout.split("\x00")
    changes = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if entry == "":
            continue
        xy, path = entry[:2], entry[3:]
        if xy[0] in ("R", "C"):
            # Rename/copy entries carry the new path in this token and the
            # path renamed/copied *from* in the next one. The contract's
            # FileEntry-like change shape has no orig_path field, so the old
            # path is consumed here and only the new path is surfaced.
            i += 1
        changes.append(
            {
                "path": path,
                "index": xy[0],
                "worktree": xy[1],
                "staged": xy[0] not in (" ", "?"),
                "untracked": xy == "??",
            }
        )
    return changes


def _tags(root: Path) -> list[str]:
    result = _git(root, "tag", "--list")
    if result.returncode != 0:
        raise GitError("git tag --list failed", returncode=result.returncode, stderr=result.stderr)
    return [line for line in result.stdout.splitlines() if line]


def info(root: Path) -> dict:
    """GitInfo per the contract, or `{"is_repo": False}`. Every field has a
    sensible value on a repo with no commits yet — no exception is raised
    just because HEAD doesn't point anywhere yet, since "just ran git init"
    is an ordinary state for a project, not an error state."""
    root = Path(root)
    if not is_repo(root):
        return {"is_repo": False}

    branch, detached = _branch_info(root)
    upstream, ahead, behind = _upstream_info(root)
    return {
        "is_repo": True,
        "branch": branch,
        "detached": detached,
        "ahead": ahead,
        "behind": behind,
        "upstream": upstream,
        "changes": _changes(root),
        "tags": _tags(root),
        "last_commit": _last_commit(root),
        "user": _user_info(root),
    }


def init(root: Path) -> dict:
    """`git init` and the resulting GitInfo. Raises `FileExistsError` when
    `root` is already a repo — the server maps that to `409 exists`."""
    root = Path(root)
    if is_repo(root):
        raise FileExistsError(f"already a repo: {root}")

    result = _git(root, "init")
    if result.returncode != 0:
        raise GitError("git init failed", returncode=result.returncode, stderr=result.stderr)
    return info(root)


def commit(root: Path, message: str, paths: list[str] | None) -> dict:
    """Commit and return `{"hash", "subject"}`.

    With `paths`, those exact paths are staged (`git add --`, so an
    untracked file is picked up too) and then committed with `--only`,
    which tells git to commit precisely the named paths and ignore whatever
    else happens to already be sitting in the index — anything a caller
    staged earlier through some other route is left staged, not swept into
    this commit. With `paths=None`, it is `git commit -a`: every tracked
    modification, no untracked files.

    An empty message is a `ValueError` — caught before a subprocess is even
    started, since git would otherwise happily open `$EDITOR` or, worse,
    accept an empty message non-interactively depending on config. Anything
    git itself refuses, including nothing-to-commit, is a `GitError`; this
    deliberately never passes `--allow-empty`.
    """
    root = Path(root)
    if not message or not message.strip():
        raise ValueError("commit message must not be empty")

    if paths is not None:
        if not paths:
            raise ValueError("paths must be a non-empty list, or None")
        add_result = _git(root, "add", "--", *paths)
        if add_result.returncode != 0:
            raise GitError("git add failed", returncode=add_result.returncode, stderr=add_result.stderr)
        args = ["commit", "--only", "-m", message, "--", *paths]
    else:
        args = ["commit", "-a", "-m", message]

    result = _git(root, *args)
    if result.returncode != 0:
        raise GitError("git commit failed", returncode=result.returncode, stderr=result.stderr)

    last = _last_commit(root)
    if last is None:  # should not happen after a successful commit
        raise GitError(
            "commit reported success but no commit is visible",
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return {"hash": last["hash"], "subject": last["subject"]}


def create_tag(root: Path, name: str, message: str | None) -> dict:
    """Create a tag — annotated when `message` is given, lightweight
    otherwise — and return the updated `{"tags": [...]}`. Never `-f`: an
    existing tag name is left for git to refuse as a `GitError`."""
    root = Path(root)
    if not name or not name.strip():
        raise ValueError("tag name must not be empty")

    # `--` before the name, as `commit()` does, so a name that starts with a
    # dash is a tag name and not a flag.
    args = ["tag", "-a", "-m", message, "--", name] if message else ["tag", "--", name]
    result = _git(root, *args)
    if result.returncode != 0:
        raise GitError("git tag failed", returncode=result.returncode, stderr=result.stderr)
    return {"tags": _tags(root)}


def check_ignore(root: Path, rel_paths: list[str]) -> set[str]:
    """Which of `rel_paths` git ignores, in one `check-ignore --stdin -z`
    call for the whole list rather than one process per path. Exit code 1
    means none of them matched, which is success, not failure — only a
    different exit code (typically 128, a real usage error) is a
    `GitError`."""
    root = Path(root)
    if not rel_paths:
        return set()

    stdin_data = "\x00".join(rel_paths) + "\x00"
    result = _git(root, "check-ignore", "--stdin", "-z", input=stdin_data)
    if result.returncode not in (0, 1):
        raise GitError("git check-ignore failed", returncode=result.returncode, stderr=result.stderr)
    return {p for p in result.stdout.split("\x00") if p}


def read_gitignore(root: Path) -> dict:
    """`{"content", "exists"}` for `<root>/.gitignore`. A plain file read,
    not a git call — `.gitignore` is a fixed path with no client input
    involved, so there is nothing here for `resolve_in_project` to guard."""
    path = Path(root) / ".gitignore"
    if not path.is_file():
        return {"content": "", "exists": False}
    return {"content": path.read_text(encoding="utf-8", errors="replace"), "exists": True}


def write_gitignore(root: Path, content: str) -> None:
    """Write `<root>/.gitignore` through a temp file and a rename, the same
    atomic-write pattern `config.py` and `project.py` use, so a failed
    write cannot truncate an existing `.gitignore`."""
    path = Path(root) / ".gitignore"
    tmp = path.with_name(".gitignore.codinian-tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
