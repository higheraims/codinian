"""The guard on the test suite itself.

Five modules pick a path out of `Path.home()` when they are imported: the
config file, the session database, the project registry, the template store and
the CLI's transcript directory. conftest.py repoints HOME before any of them is
imported so a test run cannot write to the user's own data. If that ever stops
working, the failure is silent and expensive -- a run would happily overwrite a
real session database -- so it is checked rather than assumed.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest

import claude_history
import config
import db
import project
import templates

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.mark.parametrize("described, path", [
    ("config file", config.CONFIG_PATH),
    ("session database", db.DB_PATH),
    ("project registry", project.REGISTRY_PATH),
    ("template store", templates.TEMPLATES_PATH),
    ("transcript directory", claude_history.PROJECTS_DIR),
])
def test_no_module_path_points_at_the_real_home(described, path):
    assert not path.is_relative_to(REAL_HOME), f"{described} would write to {path}"
    assert path.is_relative_to(Path(os.environ["HOME"])), described


def test_the_sandbox_home_is_not_the_real_home():
    assert Path(os.environ["HOME"]) != REAL_HOME


def test_git_reads_no_config_from_this_machine():
    # vcs.py shells out to the real git binary. Without this, a developer's
    # own ~/.gitconfig decides what a test repo's committer identity is.
    assert Path(os.environ["GIT_CONFIG_GLOBAL"]).is_relative_to(Path(os.environ["HOME"]).parent)
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
