"""Shared fixtures, and the sandbox that keeps a test run off the real home.

Several modules read a path out of `Path.home()` at import time -- the session
database, the project registry, the config file, the template store, the CLI's
transcript directory. Rebinding each one per test would mean remembering all
five every time a sixth is added, and forgetting one means a test run writes to
the user's own data. So `HOME` is repointed here, at import, before any of them
is imported: this file is loaded before the test modules that import them.

That is also why there is no root conftest.py. Import-path setup lives in
`pyproject.toml` (`pythonpath = ["."]`), which leaves this file free to run the
sandbox first.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_SANDBOX = Path(tempfile.mkdtemp(prefix="codinian-tests-"))
atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)

_HOME = _SANDBOX / "home"
(_HOME / ".config").mkdir(parents=True, exist_ok=True)
(_HOME / ".local" / "share").mkdir(parents=True, exist_ok=True)

os.environ["HOME"] = str(_HOME)
os.environ["XDG_CONFIG_HOME"] = str(_HOME / ".config")
os.environ["XDG_DATA_HOME"] = str(_HOME / ".local" / "share")

# Set in a developer's shell, these would point the modules under test at a
# second real store instead of the sandbox.
for _leaked in ("CODINIAN_CONFIG", "CODINIAN_TEMPLATES_PATH", "CODINIAN_APP_ID"):
    os.environ.pop(_leaked, None)

# vcs.py shells out to the real git, which would otherwise read the developer's
# ~/.gitconfig and /etc/gitconfig. An empty global config is also what makes the
# "this repo has no committer identity" case reachable.
os.environ["GIT_CONFIG_GLOBAL"] = str(_SANDBOX / "gitconfig")
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
(_SANDBOX / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home() -> Path:
    """The sandboxed home. Tests that want to assert where a module writes can
    check against this rather than hardcoding a path."""
    return _HOME


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """An empty folder standing in for a user's project."""
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture
def git_repo(project_dir: Path) -> Path:
    """A project folder that is a git repo with a committer identity and one
    commit, which is the state most of vcs.py's functions assume."""
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"],
                   cwd=project_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=project_dir, check=True)
    (project_dir / "README.md").write_text("first\n")
    subprocess.run(["git", "add", "README.md"], cwd=project_dir, check=True)
    subprocess.run(["git", "commit", "-qm", "Initial commit"],
                   cwd=project_dir, check=True)
    return project_dir


@pytest.fixture
def write_issue(project_dir: Path):
    """Write an issue file into the project's issues/ directory and return its
    path. Front matter is passed as raw text so a test can write a file that
    does not parse."""
    def _write(filename: str, frontmatter: str, body: str = "\n## Summary\n\nBody text.\n") -> Path:
        issues_dir = project_dir / "issues"
        issues_dir.mkdir(exist_ok=True)
        path = issues_dir / filename
        path.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
        return path
    return _write
