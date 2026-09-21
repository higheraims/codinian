"""Which `claude` binary a session runs (ISSUE-060).

Two things are being pinned down here. The order: the install on this machine
wins over the copy inside the SDK, because that is the one a package manager
updates. And the one place there is no fallback at all, a path the user typed,
since stepping quietly around a pin is how somebody ends up debugging a binary
they thought they had ruled out.

The finders are stubbed in most of these. A test that asked the real
`shutil.which` would pass or fail on whether the machine running it has Claude
Code installed.
"""

from __future__ import annotations

import os

import pytest

from codinian import claude_cli


@pytest.fixture
def installed(monkeypatch):
    """Stub both finders. Call with either or both set to None for absent."""
    def _set(system: str | None = "/usr/bin/claude",
             bundled: str | None = "/site-packages/claude_agent_sdk/_bundled/claude"):
        monkeypatch.setattr(claude_cli, "system_path", lambda: system)
        monkeypatch.setattr(claude_cli, "bundled_path", lambda: bundled)
    return _set


# ------------------------------------------------------------------ choosing


def test_the_default_is_the_install_on_this_machine(installed):
    installed()
    chosen = claude_cli.resolve({})
    assert chosen.path == "/usr/bin/claude"
    assert chosen.source == "system"
    assert chosen.problem is None


def test_no_system_install_falls_back_to_the_bundled_copy(installed):
    installed(system=None)
    chosen = claude_cli.resolve({})
    assert chosen.source == "bundled"
    # A fallback the user did not choose says so, or the About tab would show a
    # version nobody can account for.
    assert chosen.problem == claude_cli.MISSING["system"]


def test_bundled_can_be_pinned(installed):
    installed()
    chosen = claude_cli.resolve({"claude_cli_source": "bundled"})
    assert chosen.path.endswith("_bundled/claude")
    assert chosen.problem is None


def test_a_package_build_has_no_bundled_copy_to_pin(installed):
    installed(bundled=None)
    chosen = claude_cli.resolve({"claude_cli_source": "bundled"})
    assert chosen.source == "system"
    assert chosen.problem == claude_cli.MISSING["bundled"]


def test_nothing_installed_leaves_the_choice_to_the_sdk(installed):
    installed(system=None, bundled=None)
    chosen = claude_cli.resolve({})
    assert chosen.path is None and chosen.source is None
    assert chosen.problem == claude_cli.NOTHING_FOUND
    # No cli_path means the SDK searches and raises its own not-found error,
    # which names the installer for the platform.
    assert claude_cli.options_kwargs({}) == {}


def test_an_unknown_source_is_read_as_the_default(installed):
    installed()
    assert claude_cli.resolve({"claude_cli_source": "whatever"}).source == "system"


def test_a_custom_path_wins_over_both(installed, tmp_path):
    installed()
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    chosen = claude_cli.resolve({"claude_cli_source": "custom",
                                 "claude_cli_path": f"  {binary}  "})
    assert chosen.path == str(binary)
    assert chosen.source == "custom"


def test_a_custom_path_that_is_not_there_falls_back_to_nothing(installed):
    installed()
    chosen = claude_cli.resolve({"claude_cli_source": "custom",
                                 "claude_cli_path": "/opt/claude/claude"})
    assert chosen.path is None
    assert "/opt/claude/claude" in chosen.problem


def test_custom_with_no_path_set_says_so(installed):
    installed()
    chosen = claude_cli.resolve({"claude_cli_source": "custom",
                                 "claude_cli_path": "   "})
    assert chosen.path is None
    assert "No path set" in chosen.problem


def test_a_non_string_path_is_ignored_rather_than_passed_on():
    assert claude_cli.custom_path({"claude_cli_path": None}) == ""
    assert claude_cli.custom_path({"claude_cli_path": 42}) == ""


def test_the_resolved_path_is_what_reaches_the_sdk(installed):
    installed()
    assert claude_cli.options_kwargs({}) == {"cli_path": "/usr/bin/claude"}


# -------------------------------------------------------------------- finding


def _fake_binary(path, output: str, status: int = 0) -> str:
    path.write_text(f"#!/bin/sh\nprintf '%s' '{output}'\nexit {status}\n")
    path.chmod(0o755)
    return str(path)


def test_a_version_is_the_first_field_of_what_the_binary_prints(tmp_path):
    binary = _fake_binary(tmp_path / "claude", "2.1.267 (Claude Code)\n")
    assert claude_cli.version(binary) == "2.1.267"


def test_a_binary_that_fails_has_no_version(tmp_path):
    binary = _fake_binary(tmp_path / "claude", "nope", status=1)
    assert claude_cli.version(binary) is None


def test_a_path_that_is_not_there_has_no_version():
    assert claude_cli.version("/no/such/claude") is None
    assert claude_cli.version(None) is None


def test_an_upgrade_under_a_running_app_is_picked_up(tmp_path):
    """The cache is keyed on the file, not the name. dnf replaces
    /usr/bin/claude with a new inode, and the About tab should not keep
    quoting the version that was there when the window opened."""
    binary = tmp_path / "claude"
    _fake_binary(binary, "2.1.235 (Claude Code)\n")
    assert claude_cli.version(str(binary)) == "2.1.235"

    _fake_binary(binary, "2.1.267 (Claude Code)\n")
    os.utime(binary, (1, 1))  # same size, so the mtime has to be what tells
    assert claude_cli.version(str(binary)) == "2.1.267"


def test_the_bundled_version_is_read_rather_than_run(monkeypatch, tmp_path):
    """Starting a 317 MB executable to ask it something the SDK already wrote
    down beside it would be the slowest row on the About tab."""
    (tmp_path / "_cli_version.py").write_text(
        '"""Bundled Claude Code CLI version."""\n\n__cli_version__ = "2.1.235"\n')
    monkeypatch.setattr(claude_cli, "_sdk_dir", lambda: tmp_path)
    assert claude_cli.bundled_version() == "2.1.235"


def test_no_sdk_means_no_bundled_anything(monkeypatch):
    monkeypatch.setattr(claude_cli, "_sdk_dir", lambda: None)
    assert claude_cli.bundled_path() is None
    assert claude_cli.bundled_version() is None
    assert claude_cli.bundled_bytes() is None


def test_an_sdk_built_from_the_sdist_carries_no_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_cli, "_sdk_dir", lambda: tmp_path)
    assert claude_cli.bundled_path() is None


def test_the_bundled_binary_is_found_beside_the_sdk(monkeypatch, tmp_path):
    bundled = tmp_path / "_bundled"
    bundled.mkdir()
    (bundled / "claude").write_text("x" * 17)
    monkeypatch.setattr(claude_cli, "_sdk_dir", lambda: tmp_path)
    assert claude_cli.bundled_path() == str(bundled / "claude")
    assert claude_cli.bundled_bytes() == 17


def test_path_is_searched_before_the_install_locations(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _name: "/usr/bin/claude")
    assert claude_cli.system_path() == "/usr/bin/claude"


def test_a_local_install_is_found_when_path_has_nothing(monkeypatch, home):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _name: None)
    local = home / ".local" / "bin"
    local.mkdir(parents=True, exist_ok=True)
    (local / "claude").write_text("#!/bin/sh\n")
    assert claude_cli.system_path() == str(local / "claude")


# -------------------------------------------------------------------- windows


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(claude_cli.platform, "system", lambda: "Windows")


def test_a_cmd_shim_on_path_is_not_a_cli(windows, monkeypatch):
    """npm's Windows install is a claude.cmd, and the SDK refuses to spawn a
    batch file: cmd.exe re-parses the command line, and %VAR% expands inside
    quotes. Handing it over as cli_path would only move the error."""
    monkeypatch.setattr(claude_cli.shutil, "which",
                        lambda name: r"C:\npm\claude.cmd" if name == "claude" else None)
    assert claude_cli.system_path() is None


def test_a_native_exe_later_on_path_beats_the_shim(windows, monkeypatch):
    paths = {"claude": r"C:\npm\claude.cmd",
             "claude.exe": r"C:\Users\me\.local\bin\claude.exe"}
    monkeypatch.setattr(claude_cli.shutil, "which", paths.get)
    assert claude_cli.system_path() == r"C:\Users\me\.local\bin\claude.exe"


def test_the_posix_locations_are_not_probed_on_windows(windows, home):
    """An extensionless ~/.local/bin/claude on Windows is a WSL or git-bash
    artifact, and a driveless /usr/local/bin/claude resolves against the
    current drive, which another local user can write to."""
    local = home / ".local" / "bin"
    local.mkdir(parents=True, exist_ok=True)
    (local / "claude").write_text("#!/bin/sh\n")
    assert claude_cli._install_locations() == [home / ".local/bin/claude.exe"]


def test_the_bundled_binary_is_named_for_the_platform(windows, monkeypatch, tmp_path):
    bundled = tmp_path / "_bundled"
    bundled.mkdir()
    (bundled / "claude").write_text("wrong platform")
    monkeypatch.setattr(claude_cli, "_sdk_dir", lambda: tmp_path)
    assert claude_cli.bundled_path() is None

    (bundled / "claude.exe").write_text("right one")
    assert claude_cli.bundled_path() == str(bundled / "claude.exe")


# ------------------------------------------------------------------ reporting


@pytest.mark.parametrize("count, shown", [
    (None, ""),
    (0, ""),
    (345_000, "345 kB"),
    (330_946_864, "331 MB"),
    (2_100_000_000, "2.1 GB"),
])
def test_sizes_are_written_the_way_a_package_manager_writes_them(count, shown):
    assert claude_cli.human_bytes(count) == shown


def test_the_about_tab_is_told_about_both_copies(installed, monkeypatch):
    installed()
    monkeypatch.setattr(claude_cli, "version", lambda path: "2.1.267" if path else None)
    monkeypatch.setattr(claude_cli, "bundled_version", lambda: "2.1.235")
    monkeypatch.setattr(claude_cli, "bundled_bytes", lambda: 330_946_864)

    info = claude_cli.describe({})
    assert info["in_use"]["source"] == "system"
    assert info["in_use"]["version"] == "2.1.267"
    assert info["bundled"]["version"] == "2.1.235"
    # The one number that decides whether it is worth keeping.
    assert info["bundled"]["bytes"] == 330_946_864
    assert info["bundled"]["in_use"] is False


def test_the_running_copy_is_reported_by_the_sdks_own_record(installed, monkeypatch):
    installed()
    monkeypatch.setattr(claude_cli, "bundled_version", lambda: "2.1.235")
    asked: list[str] = []
    monkeypatch.setattr(claude_cli, "version", lambda path: asked.append(path))

    info = claude_cli.describe({"claude_cli_source": "bundled"})
    assert info["in_use"]["version"] == "2.1.235"
    assert info["bundled"]["in_use"] is True
    # One subprocess, for the row about the system install. The bundled binary
    # is never started to be asked what it already says on paper.
    assert asked == ["/usr/bin/claude"]
