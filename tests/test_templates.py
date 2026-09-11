"""Session templates, per-folder defaults, and the permission-mode precedence.

`resolve_permission_mode` exists so the order is one thing to test rather than
logic repeated across UI callbacks, so the order is what gets tested here:
prior mode, then template, then project settings, then the remembered folder
mode, then the global fallback.
"""

from __future__ import annotations

import json

import pytest

import project
import templates


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "TEMPLATES_PATH", tmp_path / "templates.json")


# ---------------------------------------------------------- named templates

def test_a_saved_template_comes_back(tmp_path):
    saved = templates.save_template("Quick edit", workdir=str(tmp_path),
                                    permission_mode="acceptEdits")
    assert saved == {"name": "Quick edit", "workdir": str(tmp_path),
                     "permission_mode": "acceptEdits"}
    assert templates.get_template("Quick edit") == saved


def test_templates_keep_the_order_they_were_created_in():
    for name in ("third", "first", "second"):
        templates.save_template(name)
    assert [t["name"] for t in templates.list_templates()] == ["third", "first", "second"]


def test_saving_the_same_name_replaces_it(tmp_path):
    templates.save_template("Dup", permission_mode="plan")
    templates.save_template("Dup", permission_mode="default")
    assert [t["permission_mode"] for t in templates.list_templates()] == ["default"]


def test_an_empty_template_name_is_refused():
    with pytest.raises(ValueError):
        templates.save_template("   ")


def test_a_template_name_is_trimmed():
    assert templates.save_template("  Padded  ")["name"] == "Padded"


def test_an_unknown_permission_mode_is_stored_as_default():
    assert templates.save_template("Odd", permission_mode="yolo")["permission_mode"] == "default"


def test_get_template_returns_none_for_an_unknown_name():
    assert templates.get_template("never saved") is None


def test_delete_reports_whether_it_removed_anything():
    templates.save_template("Doomed")
    assert templates.delete_template("Doomed") is True
    assert templates.delete_template("Doomed") is False


def test_get_template_hands_back_a_copy():
    templates.save_template("Shared")
    fetched = templates.get_template("Shared")
    fetched["permission_mode"] = "bypassPermissions"
    assert templates.get_template("Shared")["permission_mode"] == "default"


# ------------------------------------------------------------ store health

def test_a_missing_store_reads_as_empty():
    assert templates.list_templates() == []
    assert templates.folder_mode("/tmp") is None


@pytest.mark.parametrize("content", ["{not json", '["a list"]', ""])
def test_a_corrupt_store_degrades_to_empty_rather_than_raising(content):
    templates.TEMPLATES_PATH.write_text(content)
    assert templates.list_templates() == []


def test_a_malformed_entry_is_dropped_and_the_rest_survive():
    templates.TEMPLATES_PATH.write_text(json.dumps({"version": 1, "templates": [
        {"name": "good", "workdir": "/tmp", "permission_mode": "plan"},
        {"name": "", "workdir": "/tmp"},
        {"workdir": "/tmp"},
        "not a dict",
        {"name": "coerced", "workdir": 42, "permission_mode": "nonsense"},
    ], "folder_defaults": {}}))

    loaded = templates.list_templates()
    assert [t["name"] for t in loaded] == ["good", "coerced"]
    assert loaded[1] == {"name": "coerced", "workdir": "", "permission_mode": "default"}


def test_a_folder_default_with_an_invalid_mode_is_dropped_on_read():
    templates.TEMPLATES_PATH.write_text(json.dumps({
        "version": 1, "templates": [],
        "folder_defaults": {"/tmp/a": "plan", "/tmp/b": "nonsense"}}))
    assert templates.folder_mode("/tmp/a") == "plan"
    assert templates.folder_mode("/tmp/b") is None


def test_writing_leaves_no_temp_file_behind():
    templates.save_template("One")
    assert [p.name for p in templates.TEMPLATES_PATH.parent.iterdir()] == ["templates.json"]


# --------------------------------------------------------- folder defaults

def test_a_recorded_folder_mode_comes_back(project_dir):
    templates.record_folder_mode(str(project_dir), "acceptEdits")
    assert templates.folder_mode(str(project_dir)) == "acceptEdits"


def test_a_folder_is_keyed_on_its_resolved_path(project_dir, tmp_path):
    templates.record_folder_mode(str(project_dir) + "/", "plan")
    link = tmp_path / "link"
    link.symlink_to(project_dir)
    assert templates.folder_mode(str(link)) == "plan"
    assert templates.folder_mode(str(project_dir / "sub" / "..")) == "plan"


def test_an_invalid_mode_is_not_recorded(project_dir):
    templates.record_folder_mode(str(project_dir), "yolo")
    assert templates.folder_mode(str(project_dir)) is None


def test_recording_again_overwrites(project_dir):
    templates.record_folder_mode(str(project_dir), "plan")
    templates.record_folder_mode(str(project_dir), "default")
    assert templates.folder_mode(str(project_dir)) == "default"


# -------------------------------------------------------------- precedence

def test_a_prior_mode_wins_over_everything(project_dir):
    # Resuming continues a conversation under the terms it was already on.
    project.save_settings(project_dir, {"default_permission_mode": "plan"})
    templates.record_folder_mode(str(project_dir), "acceptEdits")
    resolved = templates.resolve_permission_mode(
        config={"default_permission_mode": "default"},
        workdir=str(project_dir),
        template={"permission_mode": "bypassPermissions"},
        prior_mode="dontAsk")
    assert resolved == "dontAsk"


def test_an_invalid_prior_mode_falls_through(project_dir):
    templates.record_folder_mode(str(project_dir), "acceptEdits")
    assert templates.resolve_permission_mode(
        config={}, workdir=str(project_dir), prior_mode="nonsense") == "acceptEdits"


def test_a_template_wins_over_project_and_folder(project_dir):
    project.save_settings(project_dir, {"default_permission_mode": "plan"})
    templates.record_folder_mode(str(project_dir), "acceptEdits")
    assert templates.resolve_permission_mode(
        config={}, workdir=str(project_dir),
        template={"permission_mode": "bypassPermissions"}) == "bypassPermissions"


def test_a_template_with_no_usable_mode_falls_through(project_dir):
    templates.record_folder_mode(str(project_dir), "plan")
    assert templates.resolve_permission_mode(
        config={}, workdir=str(project_dir), template={"permission_mode": "junk"}) == "plan"
    assert templates.resolve_permission_mode(
        config={}, workdir=str(project_dir), template={}) == "plan"


def test_project_settings_win_over_the_remembered_folder_mode(project_dir):
    project.save_settings(project_dir, {"default_permission_mode": "plan"})
    templates.record_folder_mode(str(project_dir), "acceptEdits")
    assert templates.resolve_permission_mode(config={}, workdir=str(project_dir)) == "plan"


def test_a_repo_that_never_said_does_not_mask_the_global_setting(project_dir):
    # load_settings would merge in "default" and make this indistinguishable
    # from a repo that chose it; settings_override is what keeps them apart.
    project.save_settings(project_dir, {"name": "Has settings, no mode"})
    (project_dir / ".codinian" / "settings.json").write_text(
        json.dumps({"name": "Has settings, no mode"}))

    assert templates.resolve_permission_mode(
        config={"default_permission_mode": "acceptEdits"},
        workdir=str(project_dir)) == "acceptEdits"


def test_the_folder_mode_covers_a_folder_that_is_not_a_project(project_dir):
    templates.record_folder_mode(str(project_dir), "acceptEdits")
    assert templates.resolve_permission_mode(
        config={"default_permission_mode": "plan"}, workdir=str(project_dir)) == "acceptEdits"


def test_the_global_setting_is_the_last_resort(project_dir):
    assert templates.resolve_permission_mode(
        config={"default_permission_mode": "dontAsk"}, workdir=str(project_dir)) == "dontAsk"


def test_with_nothing_configured_anywhere_the_answer_is_default(project_dir):
    assert templates.resolve_permission_mode(config={}, workdir=str(project_dir)) == "default"
