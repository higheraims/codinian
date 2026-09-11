"""The project registry, per-repo settings, and how the issues directory is
found.

`_is_contained_relative` gets the most attention here. `issues.dir` is the one
project-relative path that does not pass through `files.resolve_in_project`,
and a client can write it, so it is the place a traversal would land.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import project


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Each test gets its own registry file. Without this they share the
    sandbox's one and the order they run in starts to matter."""
    monkeypatch.setattr(project, "REGISTRY_PATH", tmp_path / "projects.json")


# ----------------------------------------------------------------- ids

def test_project_id_is_stable_across_spellings_of_the_same_folder(project_dir):
    canonical = project.project_id(project_dir)
    assert project.project_id(str(project_dir) + "/") == canonical
    assert project.project_id(project_dir / "." ) == canonical
    assert project.project_id(project_dir / "sub" / "..") == canonical


def test_project_id_follows_a_symlink_to_the_same_id(tmp_path, project_dir):
    link = tmp_path / "link-to-proj"
    link.symlink_to(project_dir)
    assert project.project_id(link) == project.project_id(project_dir)


def test_project_id_is_eight_hex_characters(project_dir):
    pid = project.project_id(project_dir)
    assert len(pid) == 8
    assert all(c in "0123456789abcdef" for c in pid)


def test_different_folders_get_different_ids(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert project.project_id(tmp_path / "a") != project.project_id(tmp_path / "b")


# ------------------------------------------------------------ registry

def test_add_project_is_idempotent(project_dir):
    first = project.add_project(str(project_dir))
    second = project.add_project(str(project_dir) + "/")
    assert first.id == second.id
    assert len(project.load_registry()) == 1


def test_add_project_rejects_a_path_that_is_not_a_directory(tmp_path):
    (tmp_path / "a-file").write_text("x")
    with pytest.raises(FileNotFoundError):
        project.add_project(str(tmp_path / "a-file"))
    with pytest.raises(FileNotFoundError):
        project.add_project(str(tmp_path / "missing"))


def test_registry_is_sorted_by_name_case_insensitively(tmp_path):
    for name in ("zebra", "Apple", "mango"):
        (tmp_path / name).mkdir()
        project.add_project(str(tmp_path / name))
    assert [p.name for p in project.load_registry()] == ["Apple", "mango", "zebra"]


def test_sort_key_breaks_a_name_tie_on_the_path():
    assert project.sort_key("app", "/home/x/a") < project.sort_key("app", "/home/x/b")
    assert project.sort_key("Apple", "/z") < project.sort_key("banana", "/a")


def test_remove_project_reports_whether_it_removed_anything(project_dir):
    added = project.add_project(str(project_dir))
    assert project.remove_project(added.id) is True
    assert project.remove_project(added.id) is False
    assert project.load_registry() == []


def test_remove_project_leaves_the_folder_alone(project_dir):
    (project_dir / "keep.txt").write_text("still here")
    added = project.add_project(str(project_dir))
    project.remove_project(added.id)
    assert (project_dir / "keep.txt").read_text() == "still here"


def test_get_project_returns_none_for_an_unknown_id():
    assert project.get_project("deadbeef") is None


@pytest.mark.parametrize("content", [
    "{not json",
    '["a list, not an object"]',
    '{"projects": "not a list"}',
    "",
])
def test_a_corrupt_registry_reads_as_empty_rather_than_raising(content, monkeypatch, tmp_path):
    path = tmp_path / "projects.json"
    path.write_text(content)
    monkeypatch.setattr(project, "REGISTRY_PATH", path)
    assert project.load_registry() == []


def test_a_malformed_entry_is_dropped_and_the_rest_survive(monkeypatch, tmp_path):
    path = tmp_path / "projects.json"
    path.write_text(json.dumps({"version": 1, "projects": [
        {"id": "aaaaaaaa", "path": "/tmp/a", "name": "a", "added_at": "2026-01-01"},
        {"id": "bbbbbbbb", "path": "/tmp/b"},          # no name, no added_at
        "not even a dict",
    ]}))
    monkeypatch.setattr(project, "REGISTRY_PATH", path)
    assert [p.id for p in project.load_registry()] == ["aaaaaaaa"]


def test_registry_generation_advances_on_every_write(project_dir):
    before = project.registry_generation()
    project.add_project(str(project_dir))
    assert project.registry_generation() > before


def test_find_project_for_path_matches_components_not_string_prefixes(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj-other").mkdir()
    proj = project.add_project(str(tmp_path / "proj"))
    other = project.add_project(str(tmp_path / "proj-other"))

    assert project.find_project_for_path(str(tmp_path / "proj-other")).id == other.id
    (tmp_path / "proj" / "deep" / "deeper").mkdir(parents=True)
    assert project.find_project_for_path(str(tmp_path / "proj" / "deep" / "deeper")).id == proj.id
    assert project.find_project_for_path(str(tmp_path)) is None


def test_find_project_for_path_prefers_the_longest_match(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    project.add_project(str(outer))
    nested = project.add_project(str(inner))
    assert project.find_project_for_path(str(inner)).id == nested.id


# ----------------------------------------------------------- meta

def test_meta_reports_existence_and_repo_state(project_dir):
    proj = project.add_project(str(project_dir))
    meta = proj.meta()
    assert meta["exists"] is True
    assert meta["is_repo"] is False

    (project_dir / ".git").mkdir()
    assert proj.meta()["is_repo"] is True


def test_meta_counts_a_git_file_as_a_repo(project_dir):
    # A worktree or submodule has a .git file pointing elsewhere, not a
    # directory.
    proj = project.add_project(str(project_dir))
    (project_dir / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    assert proj.meta()["is_repo"] is True


def test_meta_of_a_deleted_folder_says_so_instead_of_raising(tmp_path):
    gone = tmp_path / "gone"
    gone.mkdir()
    proj = project.add_project(str(gone))
    gone.rmdir()
    meta = proj.meta()
    assert meta["exists"] is False
    assert meta["issues_dir"] == "issues"


# -------------------------------------------------------------- settings

def test_defaults_are_returned_when_no_settings_file_exists(project_dir):
    assert project.load_settings(project_dir) == project.DEFAULT_SETTINGS


def test_defaults_are_returned_as_a_copy_not_the_shared_dict(project_dir):
    loaded = project.load_settings(project_dir)
    loaded["issues"]["areas"].append("mutated")
    assert "mutated" not in project.DEFAULT_SETTINGS["issues"]["areas"]


def test_a_partial_settings_file_keeps_every_other_default(project_dir):
    (project_dir / ".codinian").mkdir()
    (project_dir / ".codinian" / "settings.json").write_text(
        json.dumps({"issues": {"areas": ["gui", "cli"]}}))

    settings = project.load_settings(project_dir)
    assert settings["issues"]["areas"] == ["gui", "cli"]
    assert settings["issues"]["id_prefix"] == "ISSUE"
    assert settings["issues"]["statuses"] == project.DEFAULT_SETTINGS["issues"]["statuses"]


@pytest.mark.parametrize("dirname", project.SETTINGS_DIRS)
def test_settings_are_read_from_any_of_the_three_former_app_names(project_dir, dirname):
    (project_dir / dirname).mkdir()
    (project_dir / dirname / "settings.json").write_text(json.dumps({"name": "found"}))
    assert project.load_settings(project_dir)["name"] == "found"


def test_the_newest_directory_name_wins_when_two_exist(project_dir):
    for dirname, name in ((".codinian", "current"), (".claudius", "ancient")):
        (project_dir / dirname).mkdir()
        (project_dir / dirname / "settings.json").write_text(json.dumps({"name": name}))
    assert project.load_settings(project_dir)["name"] == "current"


@pytest.mark.parametrize("content", ["{not json", '"a string"', ""])
def test_a_corrupt_settings_file_falls_back_to_defaults(project_dir, content):
    (project_dir / ".codinian").mkdir()
    (project_dir / ".codinian" / "settings.json").write_text(content)
    assert project.load_settings(project_dir) == project.DEFAULT_SETTINGS


def test_settings_override_distinguishes_unset_from_set_to_the_default(project_dir):
    assert project.settings_override(project_dir, "default_permission_mode") is None

    project.save_settings(project_dir, {"default_permission_mode": "default"})
    assert project.settings_override(project_dir, "default_permission_mode") == "default"


def test_save_settings_writes_a_whole_canonical_object_from_a_fragment(project_dir):
    project.save_settings(project_dir, {"name": "My Project"})
    on_disk = json.loads((project_dir / ".codinian" / "settings.json").read_text())

    assert list(on_disk) == ["version", "name", "default_permission_mode", "issues"]
    assert list(on_disk["issues"]) == ["dir", "id_prefix", "statuses", "types", "areas", "sections"]
    assert on_disk["name"] == "My Project"
    assert on_disk["issues"]["id_prefix"] == "ISSUE"


def test_save_settings_keeps_an_unrecognised_key(project_dir):
    project.save_settings(project_dir, {"future_key": 42})
    on_disk = json.loads((project_dir / ".codinian" / "settings.json").read_text())
    assert on_disk["future_key"] == 42
    assert list(on_disk)[-1] == "future_key"


def test_save_settings_always_writes_the_current_directory_name(project_dir):
    (project_dir / ".claudius").mkdir()
    (project_dir / ".claudius" / "settings.json").write_text(json.dumps({"name": "old"}))

    project.save_settings(project_dir, {"name": "new"})
    assert (project_dir / ".codinian" / "settings.json").is_file()
    assert json.loads((project_dir / ".claudius" / "settings.json").read_text())["name"] == "old"


def test_settings_file_ends_with_a_newline_and_uses_two_space_indent(project_dir):
    project.save_settings(project_dir, {})
    text = (project_dir / ".codinian" / "settings.json").read_text()
    assert text.endswith("}\n")
    assert '\n  "version"' in text


def test_no_temp_file_is_left_behind(project_dir):
    project.save_settings(project_dir, {})
    assert [p.name for p in (project_dir / ".codinian").iterdir()] == ["settings.json"]


# ------------------------------------------------------- issues.dir safety

@pytest.mark.parametrize("bad", [
    "/etc",
    "../../elsewhere",
    "..",
    "issues/../../..",
    "sub\\dir",
    "with\x00nul",
    "   ",
])
def test_save_settings_refuses_an_issues_dir_that_leaves_the_project(project_dir, bad):
    with pytest.raises(ValueError):
        project.save_settings(project_dir, {"issues": {"dir": bad}})
    assert not (project_dir / ".codinian").exists()


@pytest.mark.parametrize("ok", ["issues", "docs/issues", "./issues", "a/b/../c"])
def test_save_settings_accepts_a_contained_relative_issues_dir(project_dir, ok):
    saved = project.save_settings(project_dir, {"issues": {"dir": ok}})
    assert saved["issues"]["dir"] == ok


def test_an_empty_issues_dir_means_unset_rather_than_invalid(project_dir):
    # "" is what DEFAULT_SETTINGS carries, so it has to write rather than
    # raise; discovery then falls through to looking on disk.
    saved = project.save_settings(project_dir, {"issues": {"dir": ""}})
    assert saved["issues"]["dir"] == ""
    assert project.resolve_issues_dir(project_dir) == "issues"


def test_a_hand_edited_escaping_issues_dir_is_ignored_on_read(project_dir):
    # save_settings refuses to write this, so the only way it gets here is a
    # hand edit or another tool. Discovery falls back to the default rather
    # than following it out of the tree.
    (project_dir / ".codinian").mkdir()
    (project_dir / ".codinian" / "settings.json").write_text(
        json.dumps({"issues": {"dir": "../../../../etc"}}))
    assert project.resolve_issues_dir(project_dir) == "issues"


# --------------------------------------------------- issues dir discovery

def test_resolve_issues_dir_prefers_the_configured_value(project_dir):
    (project_dir / "issues").mkdir()
    project.save_settings(project_dir, {"issues": {"dir": "docs/tickets"}})
    assert project.resolve_issues_dir(project_dir) == "docs/tickets"


def test_resolve_issues_dir_falls_back_to_whichever_directory_exists(project_dir):
    assert project.resolve_issues_dir(project_dir) == "issues"

    (project_dir / "docs" / "issues").mkdir(parents=True)
    assert project.resolve_issues_dir(project_dir) == "docs/issues"

    (project_dir / "issues").mkdir()
    assert project.resolve_issues_dir(project_dir) == "issues"


def test_resolve_issues_dir_creates_nothing(project_dir):
    project.resolve_issues_dir(project_dir)
    assert list(project_dir.iterdir()) == []


# ------------------------------------------------------- cached resolver

def test_cached_resolver_finds_the_project_containing_a_workdir(tmp_path):
    (tmp_path / "proj" / "sub").mkdir(parents=True)
    proj = project.add_project(str(tmp_path / "proj"))
    resolve = project.cached_resolver()
    assert resolve(str(tmp_path / "proj" / "sub")) == proj.id
    assert resolve(str(tmp_path)) is None


def test_cached_resolver_sees_a_registration_made_after_it_cached(tmp_path):
    (tmp_path / "proj").mkdir()
    resolve = project.cached_resolver(ttl_seconds=3600)
    assert resolve(str(tmp_path / "proj")) is None

    # The generation counter is what makes this immediate rather than a wait
    # for the TTL.
    proj = project.add_project(str(tmp_path / "proj"))
    assert resolve(str(tmp_path / "proj")) == proj.id
