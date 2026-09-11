"""File operations, and the containment check the remote server depends on.

`resolve_in_project` gets the bulk of this module. The server can be reached
over a tailnet, so a path that escapes the project root is a read of an
arbitrary file from someone else's machine, not a cosmetic bug.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codinian import files


# ---------------------------------------------------------- containment

@pytest.mark.parametrize("rel", [
    "..",
    "../outside.txt",
    "sub/../../outside.txt",
    "/etc/passwd",
    "/",
    "sub\\file.txt",
    "with\x00nul.txt",
    ".git",
    ".git/config",
    ".git/hooks/pre-commit",
])
def test_a_path_that_escapes_or_reaches_git_is_rejected(project_dir, rel):
    (project_dir / "sub").mkdir()
    with pytest.raises(files.PathError):
        files.resolve_in_project(project_dir, rel)


def test_a_non_string_path_is_rejected(project_dir):
    for rel in (None, 42, Path("x"), ["a"]):
        with pytest.raises(files.PathError):
            files.resolve_in_project(project_dir, rel)


@pytest.mark.parametrize("rel", ["file.txt", "sub/file.txt", "./file.txt",
                                "sub/../file.txt", "", "."])
def test_a_contained_path_resolves(project_dir, rel):
    (project_dir / "sub").mkdir()
    resolved = files.resolve_in_project(project_dir, rel)
    assert resolved.is_relative_to(project_dir.resolve())


def test_the_last_component_may_not_exist_yet(project_dir):
    # Path.resolve(strict=True) would raise here, which is the whole reason
    # this function resolves the parent instead.
    resolved = files.resolve_in_project(project_dir, "brand-new.txt")
    assert resolved == project_dir.resolve() / "brand-new.txt"


def test_a_missing_parent_directory_is_rejected(project_dir):
    with pytest.raises(files.PathError):
        files.resolve_in_project(project_dir, "nope/file.txt")


def test_a_symlink_at_the_leaf_pointing_outside_is_caught(project_dir, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("not yours")
    (project_dir / "innocent.txt").symlink_to(secret)
    with pytest.raises(files.PathError):
        files.resolve_in_project(project_dir, "innocent.txt")


def test_a_symlinked_directory_partway_up_is_caught(project_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not yours")
    (project_dir / "hop").symlink_to(outside)
    with pytest.raises(files.PathError):
        files.resolve_in_project(project_dir, "hop/secret.txt")


def test_a_symlink_that_stays_inside_the_project_is_allowed(project_dir):
    (project_dir / "real").mkdir()
    (project_dir / "real" / "f.txt").write_text("mine")
    (project_dir / "link").symlink_to(project_dir / "real")
    resolved = files.resolve_in_project(project_dir, "link/f.txt")
    assert resolved.read_text() == "mine"


def test_a_symlink_to_git_is_caught(project_dir):
    (project_dir / ".git").mkdir()
    (project_dir / ".git" / "config").write_text("[core]\n")
    (project_dir / "shortcut").symlink_to(project_dir / ".git")
    with pytest.raises(files.PathError):
        files.resolve_in_project(project_dir, "shortcut/config")


def test_a_file_merely_named_like_git_is_allowed(project_dir):
    (project_dir / ".gitignore").write_text("*.pyc\n")
    assert files.resolve_in_project(project_dir, ".gitignore").name == ".gitignore"
    (project_dir / "sub").mkdir()
    (project_dir / "sub" / ".git-notes").write_text("x")
    files.resolve_in_project(project_dir, "sub/.git-notes")


# ------------------------------------------------------------- listing

def test_list_dir_puts_directories_first_then_files_each_alphabetical(project_dir):
    for name in ("b.txt", "A.txt", "c.txt"):
        (project_dir / name).write_text("x")
    for name in ("zdir", "Adir"):
        (project_dir / name).mkdir()

    names = [e["name"] for e in files.list_dir(project_dir, "")]
    assert names == ["Adir", "zdir", "A.txt", "b.txt", "c.txt"]


def test_list_dir_includes_hidden_entries(project_dir):
    (project_dir / ".hidden").write_text("x")
    assert ".hidden" in [e["name"] for e in files.list_dir(project_dir, "")]


def test_entries_carry_size_mtime_and_a_root_relative_path(project_dir):
    (project_dir / "sub").mkdir()
    (project_dir / "sub" / "f.txt").write_text("12345")
    entry = files.list_dir(project_dir, "sub")[0]
    assert entry["path"] == "sub/f.txt"
    assert entry["size"] == 5
    assert entry["is_dir"] is False
    assert entry["mtime"] > 0


def test_without_a_checker_the_default_ignore_names_are_marked(project_dir):
    for name in files.DEFAULT_IGNORED_NAMES:
        (project_dir / name).mkdir()
    (project_dir / "src").mkdir()

    ignored = {e["name"]: e["ignored"] for e in files.list_dir(project_dir, "")}
    assert ignored["src"] is False
    assert all(ignored[name] for name in files.DEFAULT_IGNORED_NAMES)


def test_a_checker_is_called_once_for_the_whole_listing(project_dir):
    (project_dir / "a.txt").write_text("x")
    (project_dir / "b.txt").write_text("x")
    calls = []

    def checker(paths):
        calls.append(list(paths))
        return {"b.txt"}

    entries = {e["name"]: e["ignored"] for e in files.list_dir(project_dir, "", checker)}
    assert len(calls) == 1
    assert sorted(calls[0]) == ["a.txt", "b.txt"]
    assert entries == {"a.txt": False, "b.txt": True}


def test_git_is_marked_ignored_even_though_check_ignore_never_reports_it(project_dir):
    (project_dir / ".git").mkdir()
    entries = {e["name"]: e["ignored"] for e in files.list_dir(project_dir, "", lambda paths: set())}
    assert entries[".git"] is True


def test_a_broken_symlink_is_dropped_rather_than_failing_the_listing(project_dir):
    (project_dir / "good.txt").write_text("x")
    (project_dir / "dangling").symlink_to(project_dir / "nothing-here")
    assert [e["name"] for e in files.list_dir(project_dir, "")] == ["good.txt"]


def test_list_dir_distinguishes_missing_from_not_a_directory(project_dir):
    (project_dir / "f.txt").write_text("x")
    with pytest.raises(FileNotFoundError):
        files.list_dir(project_dir, "missing")
    with pytest.raises(NotADirectoryError):
        files.list_dir(project_dir, "f.txt")


# --------------------------------------------------------------- reading

def test_read_text_file_returns_content_size_and_path(project_dir):
    (project_dir / "f.txt").write_text("hello\n")
    result = files.read_text_file(project_dir, "f.txt")
    assert result == {"path": "f.txt", "content": "hello\n", "size": 6,
                      "mtime": pytest.approx(result["mtime"])}


def test_invalid_utf8_is_replaced_rather_than_raising(project_dir):
    (project_dir / "f.txt").write_bytes(b"caf\xe9 latte")
    assert files.read_text_file(project_dir, "f.txt")["content"] == "caf� latte"


def test_a_file_over_the_size_cap_raises_with_its_size(project_dir):
    (project_dir / "big.txt").write_bytes(b"x" * (files.MAX_TEXT_BYTES + 1))
    with pytest.raises(files.FileTooLarge) as caught:
        files.read_text_file(project_dir, "big.txt")
    assert caught.value.size == files.MAX_TEXT_BYTES + 1


def test_a_file_exactly_at_the_cap_is_read(project_dir):
    (project_dir / "edge.txt").write_bytes(b"x" * files.MAX_TEXT_BYTES)
    assert len(files.read_text_file(project_dir, "edge.txt")["content"]) == files.MAX_TEXT_BYTES


def test_a_nul_byte_in_the_sniff_window_means_binary(project_dir):
    (project_dir / "bin").write_bytes(b"\x89PNG\x00\x00rest")
    with pytest.raises(files.BinaryFile):
        files.read_text_file(project_dir, "bin")


def test_a_nul_byte_past_the_sniff_window_is_not_checked(project_dir):
    # Documented behaviour: the sniff is a bounded prefix, not the whole file.
    (project_dir / "late").write_bytes(b"t" * files._BINARY_SNIFF_BYTES + b"\x00")
    assert files.read_text_file(project_dir, "late")["content"].endswith("\x00")


def test_size_is_checked_before_the_file_is_read(project_dir):
    # A huge binary file should raise FileTooLarge, not BinaryFile: the order
    # is what keeps it from being read twice.
    (project_dir / "huge.bin").write_bytes(b"\x00" * (files.MAX_TEXT_BYTES + 10))
    with pytest.raises(files.FileTooLarge):
        files.read_text_file(project_dir, "huge.bin")


def test_reading_distinguishes_missing_from_a_directory(project_dir):
    (project_dir / "d").mkdir()
    with pytest.raises(FileNotFoundError):
        files.read_text_file(project_dir, "missing.txt")
    with pytest.raises(IsADirectoryError):
        files.read_text_file(project_dir, "d")


# --------------------------------------------------------------- writing

def test_write_text_file_creates_and_overwrites(project_dir):
    files.write_text_file(project_dir, "f.txt", "first")
    result = files.write_text_file(project_dir, "f.txt", "second")
    assert (project_dir / "f.txt").read_text() == "second"
    assert result == {"path": "f.txt", "size": 6, "mtime": pytest.approx(result["mtime"])}


def test_writing_leaves_no_temp_file_behind(project_dir):
    files.write_text_file(project_dir, "f.txt", "x")
    assert [p.name for p in project_dir.iterdir()] == ["f.txt"]


def test_writing_does_not_create_parent_directories(project_dir):
    with pytest.raises(files.PathError):
        files.write_text_file(project_dir, "missing/f.txt", "x")


def test_writing_refuses_a_path_outside_the_project(project_dir, tmp_path):
    with pytest.raises(files.PathError):
        files.write_text_file(project_dir, "../escaped.txt", "x")
    assert not (tmp_path / "escaped.txt").exists()


# --------------------------------------------------------- create/rename

def test_create_entry_makes_a_file_or_a_directory(project_dir):
    assert files.create_entry(project_dir, "new.txt", "file") == {"path": "new.txt"}
    assert (project_dir / "new.txt").is_file()
    assert files.create_entry(project_dir, "newdir", "dir") == {"path": "newdir"}
    assert (project_dir / "newdir").is_dir()


def test_create_entry_rejects_an_unknown_kind(project_dir):
    with pytest.raises(ValueError):
        files.create_entry(project_dir, "x", "symlink")


def test_create_entry_refuses_to_clobber(project_dir):
    (project_dir / "taken.txt").write_text("mine")
    with pytest.raises(FileExistsError):
        files.create_entry(project_dir, "taken.txt", "file")
    assert (project_dir / "taken.txt").read_text() == "mine"


def test_create_entry_refuses_a_dangling_symlink_name(project_dir):
    # PathError rather than FileExistsError, and the server answers 400 rather
    # than 409. A symlink with no target cannot be checked for containment --
    # the target it would acquire may be anywhere -- so it is refused at the
    # boundary and never reaches the "already exists" test.
    (project_dir / "link").symlink_to(project_dir / "nothing")
    with pytest.raises(files.PathError):
        files.create_entry(project_dir, "link", "file")
    assert (project_dir / "link").is_symlink()


def test_rename_moves_within_the_project(project_dir):
    (project_dir / "sub").mkdir()
    (project_dir / "a.txt").write_text("x")
    assert files.rename_entry(project_dir, "a.txt", "sub/b.txt") == {"path": "sub/b.txt"}
    assert (project_dir / "sub" / "b.txt").read_text() == "x"


def test_rename_refuses_to_overwrite_the_destination(project_dir):
    (project_dir / "a.txt").write_text("a")
    (project_dir / "b.txt").write_text("b")
    with pytest.raises(FileExistsError):
        files.rename_entry(project_dir, "a.txt", "b.txt")
    assert (project_dir / "b.txt").read_text() == "b"


def test_rename_reports_a_missing_source(project_dir):
    with pytest.raises(FileNotFoundError):
        files.rename_entry(project_dir, "missing.txt", "other.txt")


def test_rename_refuses_a_destination_outside_the_project(project_dir):
    (project_dir / "a.txt").write_text("x")
    with pytest.raises(files.PathError):
        files.rename_entry(project_dir, "a.txt", "../escaped.txt")
    assert (project_dir / "a.txt").is_file()


def test_open_external_checks_the_path_before_spawning(project_dir, monkeypatch):
    spawned = []
    monkeypatch.setattr(files.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    with pytest.raises(files.PathError):
        files.open_external(project_dir, "../outside")
    with pytest.raises(FileNotFoundError):
        files.open_external(project_dir, "missing.txt")
    assert spawned == []

    (project_dir / "doc.pdf").write_text("x")
    files.open_external(project_dir, "doc.pdf")
    assert spawned[0][0][0] == "xdg-open"
