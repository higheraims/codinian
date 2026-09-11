"""Git operations, run against real repositories in a temp directory.

Nothing here is mocked. vcs.py's whole design is that the real `git` binary
does the work, so a test that stubbed subprocess would only check that the
arguments were spelled the way the test expects.

The suite's git config is sandboxed in conftest.py (GIT_CONFIG_GLOBAL to an
empty file, GIT_CONFIG_NOSYSTEM), so a repo has no committer identity until a
test gives it one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import vcs


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def message(root: Path) -> str:
    """The last commit's full message. `git log --format=%B` appends a
    separating newline of its own, which is not part of the message."""
    return git(root, "log", "-1", "--format=%B").rstrip("\n")


# ---------------------------------------------------------------- is_repo

def test_is_repo_is_about_this_folder_not_an_ancestor(project_dir):
    assert vcs.is_repo(project_dir) is False
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)
    assert vcs.is_repo(project_dir) is True

    nested = project_dir / "nested"
    nested.mkdir()
    # Inside a repo, but not a repo root. A project registered as a subfolder
    # of someone's checkout should not adopt the ancestor's history.
    assert vcs.is_repo(nested) is False


def test_a_git_file_counts_as_a_repo(project_dir):
    (project_dir / ".git").write_text("gitdir: /elsewhere\n")
    assert vcs.is_repo(project_dir) is True


# ------------------------------------------------------------------- info

def test_info_on_a_plain_folder_says_only_that_it_is_not_a_repo(project_dir):
    assert vcs.info(project_dir) == {"is_repo": False}


def test_info_on_a_fresh_repo_with_no_commits_does_not_raise(project_dir):
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)
    info = vcs.info(project_dir)
    assert info["is_repo"] is True
    assert info["last_commit"] is None
    assert info["detached"] is False
    assert info["upstream"] is None
    assert (info["ahead"], info["behind"]) == (0, 0)
    assert info["changes"] == []
    assert info["tags"] == []


def test_info_reports_branch_and_last_commit(git_repo):
    info = vcs.info(git_repo)
    assert info["branch"] == "main"
    assert info["last_commit"]["subject"] == "Initial commit"
    assert len(info["last_commit"]["hash"]) == 40
    assert info["last_commit"]["date"].startswith("20")


def test_info_reports_no_user_when_the_repo_has_no_identity(project_dir):
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)
    assert vcs.info(project_dir)["user"] is None


def test_info_reports_the_committer_identity_when_set(git_repo):
    assert vcs.info(git_repo)["user"] == {"name": "Test Committer",
                                          "email": "test@example.invalid"}


def test_info_reports_a_detached_head(git_repo):
    head = git(git_repo, "rev-parse", "HEAD").strip()
    subprocess.run(["git", "-C", str(git_repo), "checkout", "-q", head], check=True)
    info = vcs.info(git_repo)
    assert info["detached"] is True
    assert head.startswith(info["branch"])  # the short hash stands in for a name


def test_changes_separate_staged_modified_and_untracked(git_repo):
    (git_repo / "staged.txt").write_text("new\n")
    git(git_repo, "add", "staged.txt")
    (git_repo / "README.md").write_text("edited\n")
    (git_repo / "untracked.txt").write_text("loose\n")

    changes = {c["path"]: c for c in vcs.info(git_repo)["changes"]}
    assert changes["staged.txt"]["staged"] is True
    assert changes["staged.txt"]["index"] == "A"
    assert changes["README.md"]["staged"] is False
    assert changes["README.md"]["worktree"] == "M"
    assert changes["untracked.txt"]["untracked"] is True


def test_a_path_with_a_space_survives_the_porcelain_parse(git_repo):
    (git_repo / "two words.txt").write_text("x\n")
    assert "two words.txt" in [c["path"] for c in vcs.info(git_repo)["changes"]]


def test_a_rename_reports_only_the_new_path(git_repo):
    (git_repo / "before.txt").write_text("content\n")
    git(git_repo, "add", "before.txt")
    git(git_repo, "commit", "-qm", "Add before.txt")
    git(git_repo, "mv", "before.txt", "after.txt")

    paths = [c["path"] for c in vcs.info(git_repo)["changes"]]
    # The old path is consumed with the record, not surfaced as a second entry.
    assert paths == ["after.txt"]


def test_upstream_ahead_and_behind_are_counted(git_repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(git_repo, "remote", "add", "origin", str(remote))
    git(git_repo, "push", "-q", "-u", "origin", "main")

    (git_repo / "local.txt").write_text("x\n")
    git(git_repo, "add", "local.txt")
    git(git_repo, "commit", "-qm", "A local commit")

    info = vcs.info(git_repo)
    assert info["upstream"] == "origin/main"
    assert info["ahead"] == 1
    assert info["behind"] == 0


# ------------------------------------------------------------------- init

def test_init_creates_a_repo_and_returns_its_info(project_dir):
    info = vcs.init(project_dir)
    assert info["is_repo"] is True
    assert (project_dir / ".git").is_dir()


def test_init_refuses_an_existing_repo(git_repo):
    with pytest.raises(FileExistsError):
        vcs.init(git_repo)


# ----------------------------------------------------------------- commit

def test_commit_stages_everything_tracked_when_no_paths_are_given(git_repo):
    (git_repo / "README.md").write_text("edited\n")
    (git_repo / "untracked.txt").write_text("loose\n")

    result = vcs.commit(git_repo, "Edit the readme", None)
    assert result["subject"] == "Edit the readme"
    # -a picks up tracked modifications and leaves untracked files alone.
    assert [c["path"] for c in vcs.info(git_repo)["changes"]] == ["untracked.txt"]


def test_commit_with_paths_picks_up_an_untracked_file(git_repo):
    (git_repo / "new.txt").write_text("x\n")
    vcs.commit(git_repo, "Add new.txt", ["new.txt"])
    assert vcs.info(git_repo)["changes"] == []


def test_commit_with_paths_leaves_other_staged_work_staged(git_repo):
    (git_repo / "mine.txt").write_text("mine\n")
    (git_repo / "someone-elses.txt").write_text("theirs\n")
    git(git_repo, "add", "someone-elses.txt")

    vcs.commit(git_repo, "Commit only mine", ["mine.txt"])

    changes = {c["path"]: c for c in vcs.info(git_repo)["changes"]}
    assert list(changes) == ["someone-elses.txt"]
    assert changes["someone-elses.txt"]["staged"] is True


def test_a_body_becomes_the_message_under_a_blank_line(git_repo):
    (git_repo / "f.txt").write_text("x\n")
    vcs.commit(git_repo, "Subject line", ["f.txt"], body="The longer why.\nSecond line.")
    assert message(git_repo) == "Subject line\n\nThe longer why.\nSecond line."


def test_a_blank_body_is_left_off_entirely(git_repo):
    (git_repo / "f.txt").write_text("x\n")
    vcs.commit(git_repo, "Subject only", ["f.txt"], body="   \n  ")
    assert message(git_repo) == "Subject only"


def test_a_body_line_starting_with_a_hash_survives(git_repo):
    # -m implies --cleanup=whitespace, so this is not read as an editor comment.
    (git_repo / "f.txt").write_text("x\n")
    vcs.commit(git_repo, "Subject", ["f.txt"], body="#42 is the issue number")
    assert "#42 is the issue number" in message(git_repo)


@pytest.mark.parametrize("message", ["", "   ", "\n"])
def test_an_empty_commit_message_is_refused_before_git_runs(git_repo, message):
    with pytest.raises(ValueError):
        vcs.commit(git_repo, message, None)


def test_an_empty_path_list_is_refused(git_repo):
    with pytest.raises(ValueError):
        vcs.commit(git_repo, "Subject", [])


def test_nothing_to_commit_is_a_git_error_carrying_gits_own_words(git_repo):
    with pytest.raises(vcs.GitError) as caught:
        vcs.commit(git_repo, "Nothing changed", None)
    assert caught.value.returncode != 0
    assert isinstance(caught.value.stderr, str)


def test_committing_an_unchanged_path_does_not_create_an_empty_commit(git_repo):
    before = git(git_repo, "rev-parse", "HEAD")
    with pytest.raises(vcs.GitError):
        vcs.commit(git_repo, "No change here", ["README.md"])
    assert git(git_repo, "rev-parse", "HEAD") == before


# -------------------------------------------------------------------- tags

def test_a_lightweight_tag_is_created_and_listed(git_repo):
    assert vcs.create_tag(git_repo, "v0.1.0", None) == {"tags": ["v0.1.0"]}


def test_an_annotated_tag_carries_its_message(git_repo):
    vcs.create_tag(git_repo, "v0.2.0", "Second release")
    assert "Second release" in git(git_repo, "tag", "-n", "-l", "v0.2.0")


def test_a_duplicate_tag_is_refused_rather_than_forced(git_repo):
    vcs.create_tag(git_repo, "v1", None)
    with pytest.raises(vcs.GitError):
        vcs.create_tag(git_repo, "v1", None)


def test_an_empty_tag_name_is_refused(git_repo):
    with pytest.raises(ValueError):
        vcs.create_tag(git_repo, "  ", None)


def test_a_tag_name_starting_with_a_dash_is_not_read_as_a_flag(git_repo):
    # git refuses the name itself; what matters is that it reaches git as a
    # name rather than as an option.
    with pytest.raises(vcs.GitError):
        vcs.create_tag(git_repo, "--force", None)


# ------------------------------------------------------------ check_ignore

def test_check_ignore_reports_only_the_ignored_paths(git_repo):
    (git_repo / ".gitignore").write_text("*.log\nbuild/\n")
    for name in ("app.log", "keep.txt"):
        (git_repo / name).write_text("x")
    (git_repo / "build").mkdir()

    assert vcs.check_ignore(git_repo, ["app.log", "keep.txt", "build"]) == {"app.log", "build"}


def test_check_ignore_treats_no_matches_as_success(git_repo):
    # git exits 1 when nothing matched, which is not a failure.
    assert vcs.check_ignore(git_repo, ["README.md"]) == set()


def test_check_ignore_short_circuits_an_empty_list(project_dir):
    # No repo here at all, so this would fail if it reached git.
    assert vcs.check_ignore(project_dir, []) == set()


def test_check_ignore_handles_a_path_with_a_space(git_repo):
    (git_repo / ".gitignore").write_text("*.log\n")
    (git_repo / "two words.log").write_text("x")
    assert vcs.check_ignore(git_repo, ["two words.log"]) == {"two words.log"}


# -------------------------------------------------------------- gitignore

def test_reading_a_missing_gitignore_says_so(project_dir):
    assert vcs.read_gitignore(project_dir) == {"content": "", "exists": False}


def test_gitignore_round_trips(project_dir):
    vcs.write_gitignore(project_dir, "__pycache__/\n*.pyc\n")
    assert vcs.read_gitignore(project_dir) == {"content": "__pycache__/\n*.pyc\n",
                                               "exists": True}


def test_writing_gitignore_leaves_no_temp_file(project_dir):
    vcs.write_gitignore(project_dir, "x\n")
    assert [p.name for p in project_dir.iterdir()] == [".gitignore"]


def test_this_module_never_learned_to_destroy_work():
    """vcs.py promises it does not rewrite history, discard changes, or talk to
    a remote. That promise is only worth anything if it is checked."""
    source = Path(vcs.__file__).read_text()
    for forbidden in ("reset", "checkout", "clean", "push", "pull", "fetch",
                      "rebase", "--amend", "--force", "-f\"", "--allow-empty"):
        offenders = [line.strip() for line in source.splitlines()
                     if forbidden in line and "_git(" in line]
        assert offenders == [], f"{forbidden} reached a git invocation: {offenders}"
