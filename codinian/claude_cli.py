"""Which `claude` binary a session runs (ISSUE-060).

The SDK finds a CLI for itself, and its first choice is the copy inside the
wheel at `claude_agent_sdk/_bundled/claude`. That copy never moves: 0.2.140 was
published on 18 August 2026 carrying CLI 2.1.235 and will carry 2.1.235 for as
long as it exists. Anthropic's dnf repository had `/usr/bin/claude` on 2.1.267
in the same month. Codinian ran the older one and had nowhere to say so, which
is why this module also exists to be read out on the About tab.

`ClaudeAgentOptions(cli_path=...)` is the documented way to choose, and the
SDK's only version constraint is a floor it warns about rather than enforces:

    # claude_agent_sdk/_internal/transport/subprocess_cli.py
    MINIMUM_CLAUDE_CODE_VERSION = "2.0.0"

There is no matched-pair rule between the Python half and the CLI, so a system
CLI is a supported configuration rather than a workaround. Measured on SDK
0.2.140 driving 2.1.267: hooks fire, partial-message streaming arrives,
`get_server_info` answers, `set_permission_mode` round-trips, and
`AskUserQuestion` behaves as ISSUE-050 recorded it.

The default is the system install, because that is the copy a package manager
keeps current. The bundled one is the fallback for a machine that has no
`claude` of its own, which is who it was put in the wheel for.

Where the search looks is taken from the SDK's own `_find_cli`, deliberately:
two opinions about where a CLI lives is a bug waiting for the day they differ.
The one addition is nothing at all, since `/usr/bin/claude` from the RPM is
found by `shutil.which` before any of the listed locations is tried.

Nothing here imports `claude_agent_sdk`. `importlib.util.find_spec` locates the
package without loading it, so this stays checkable in a test run that never
touches the SDK, and reading the bundled version out of a file beats starting a
317 MB executable to ask it.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

# What the Binary setting holds.
#
#   system   the install a package manager keeps current, bundled as fallback
#   bundled  the copy inside the installed SDK, system as fallback
#   custom   one path the user named, and no fallback: a pin nobody asked to
#            be quietly stepped around
SOURCES = ("system", "bundled", "custom")

DEFAULTS = {
    "claude_cli_source": "system",
    "claude_cli_path": "",
}

# Long enough for a cold start off spinning rust, short enough that the
# Settings tab does not sit there. The binary is 317 MB on the bundled path and
# 120 ms warm on this machine.
VERSION_TIMEOUT = 5.0

# `claude --version` prints "2.1.267 (Claude Code)".
_VERSION_LINE = re.compile(r"^\s*(\d[^\s]*)")

_SDK_CLI_VERSION = re.compile(r"""__cli_version__\s*=\s*["']([^"']+)["']""")

# Answers keyed by the file's identity rather than its name, so an upgrade under
# a running app is picked up: dnf replaces /usr/bin/claude with a new inode and
# the stat moves with it. A session already running holds the old one, which is
# the behaviour that makes an upgrade mid-session safe.
_versions: dict[tuple[str, int, int], str | None] = {}


@dataclass(frozen=True)
class Resolution:
    """What a session will be started with.

    `path` is None when nothing was found, which is not an error here: the SDK
    then searches for itself and raises `CLINotFoundError` with install
    instructions for the platform, and that message is better than any this
    module could write.
    """

    path: str | None
    # Which of SOURCES the path came from, or None when there is no path.
    source: str | None
    # The setting that was in force, so a fallback can be described as one.
    asked_for: str
    # Why the answer is not the one asked for, in a sentence fit for the UI.
    problem: str | None


# ------------------------------------------------------------------ settings


def source(config: dict) -> str:
    value = config.get("claude_cli_source", DEFAULTS["claude_cli_source"])
    return value if value in SOURCES else DEFAULTS["claude_cli_source"]


def custom_path(config: dict) -> str:
    value = config.get("claude_cli_path", "")
    return value.strip() if isinstance(value, str) else ""


# ------------------------------------------------------------------- finding


def _windows() -> bool:
    return platform.system() == "Windows"


def _binary_name() -> str:
    return "claude.exe" if _windows() else "claude"


def _is_native_exe(path: str) -> bool:
    """Whether Windows would run this image directly.

    npm's Windows install is a `claude.cmd` shim, and the SDK refuses to spawn
    a batch file at all: Windows runs one through `cmd.exe`, which re-parses
    the whole command line, and `%VAR%` expands even inside quotes. So a shim
    is not a CLI as far as this app is concerned.
    """
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.rstrip(". ").lower().endswith((".exe", ".com"))


def _install_locations() -> list[Path]:
    """Where the SDK looks after PATH, copied from its `_find_cli`.

    The POSIX entries are deliberately not probed on Windows. An
    extensionless `~/.local/bin/claude` there is a WSL or git-bash artifact,
    and a driveless `/usr/local/bin/claude` resolves against the current
    drive, which is a directory another local user can create.
    """
    home = Path.home()
    if _windows():
        return [home / ".local/bin/claude.exe"]
    return [
        home / ".npm-global/bin/claude",
        Path("/usr/local/bin/claude"),
        home / ".local/bin/claude",
        home / "node_modules/.bin/claude",
        home / ".yarn/bin/claude",
        home / ".claude/local/claude",
    ]


def system_path() -> str | None:
    """The `claude` this machine has of its own, or None.

    PATH first, which is what finds the RPM's `/usr/bin/claude` and any
    `claude.exe` the Windows native installer put on PATH.
    """
    hit = shutil.which("claude")
    if hit and (not _windows() or _is_native_exe(hit)):
        return hit

    # Windows resolved something CreateProcess cannot run: PATHEXT prefers
    # .EXE within one directory, so a shim only wins by sitting in an earlier
    # one. Ask for the executable by name before giving up on PATH.
    if hit and _windows():
        exe = shutil.which("claude.exe")
        if exe and _is_native_exe(exe):
            return exe

    for path in _install_locations():
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    # A shim-only machine ends up here rather than with the shim, so the SDK
    # gets to raise its own refusal, which explains the cmd.exe problem and
    # names the installer that fixes it.
    return None


def _sdk_dir() -> Path | None:
    try:
        spec = find_spec("claude_agent_sdk")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).parent


def bundled_path() -> str | None:
    """The CLI inside the installed SDK, or None when there is none.

    None is the ordinary answer for a package built from the source
    distribution, which is what the RPM and the documented `pip --no-binary`
    install both produce. The binary is absent by choice there, not missing.
    """
    sdk = _sdk_dir()
    if sdk is None:
        return None
    path = sdk / "_bundled" / _binary_name()
    try:
        return str(path) if path.is_file() else None
    except OSError:
        return None


def bundled_version() -> str | None:
    """Which CLI the installed wheel carries, read from the SDK's own
    `_cli_version.py`. Only meaningful alongside a `bundled_path`: the file
    ships in the sdist too, where it names a binary that was left out."""
    sdk = _sdk_dir()
    if sdk is None:
        return None
    try:
        text = (sdk / "_cli_version.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = _SDK_CLI_VERSION.search(text)
    return match.group(1) if match else None


def bundled_bytes() -> int | None:
    path = bundled_path()
    if path is None:
        return None
    try:
        return os.stat(path).st_size
    except OSError:
        return None


# ------------------------------------------------------------------ choosing


MISSING = {
    "system": "No claude on this machine, so the copy bundled with the SDK is "
              "running instead.",
    "bundled": "The installed SDK carries no CLI, which is normal for one built "
               "from the source distribution, so the system install is running.",
}

NOTHING_FOUND = ("No claude binary found. Install Claude Code, or point this at "
                 "one.")


def resolve(config: dict) -> Resolution:
    """Which binary this config means, and what went wrong if anything did."""
    asked = source(config)

    if asked == "custom":
        chosen = custom_path(config)
        if not chosen:
            return Resolution(None, None, asked,
                              "No path set, so the SDK is choosing the binary.")
        try:
            usable = Path(chosen).is_file()
        except OSError:
            usable = False
        if not usable:
            return Resolution(None, None, asked,
                              f"No file at {chosen}, so the SDK is choosing "
                              "the binary.")
        return Resolution(chosen, "custom", asked, None)

    order = ("system", "bundled") if asked == "system" else ("bundled", "system")
    finders = {"system": system_path, "bundled": bundled_path}
    for kind in order:
        found = finders[kind]()
        if found is None:
            continue
        if kind == order[0]:
            return Resolution(found, kind, asked, None)
        return Resolution(found, kind, asked, MISSING[order[0]])

    return Resolution(None, None, asked, NOTHING_FOUND)


def options_kwargs(config: dict) -> dict:
    """The `cli_path` keyword for `ClaudeAgentOptions`, or nothing.

    Nothing is a real answer: it leaves the SDK to search, which is what
    produces a `CLINotFoundError` carrying install instructions for the
    platform rather than a path error from us.
    """
    chosen = resolve(config)
    return {"cli_path": chosen.path} if chosen.path else {}


# ------------------------------------------------------------------ reporting


def version(path: str | None) -> str | None:
    """What `claude --version` reports for a binary, or None if it will not
    say. Cached per file, since the answer only changes when the file does."""
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, stat.st_mtime_ns, stat.st_size)
    if key not in _versions:
        _versions[key] = _probe(path)
    return _versions[key]


def _probe(path: str) -> str | None:
    try:
        result = subprocess.run([path, "--version"], capture_output=True,
                                text=True, timeout=VERSION_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = _VERSION_LINE.match(result.stdout or "")
    return match.group(1) if match else None


def describe(config: dict) -> dict:
    """Everything the About tab shows: what is running, and what else is here.

    The bundled entry carries its size because that is the number that decides
    whether it is worth keeping. Its version is read rather than run, so this
    costs one subprocess at most.
    """
    chosen = resolve(config)
    system = system_path()
    bundled = bundled_path()
    return {
        "in_use": {
            "path": chosen.path,
            "source": chosen.source,
            "version": (bundled_version() if chosen.source == "bundled"
                        else version(chosen.path)),
            "problem": chosen.problem,
        },
        "system": {"path": system, "version": version(system)},
        "bundled": {"path": bundled,
                    "version": bundled_version() if bundled else None,
                    "bytes": bundled_bytes(),
                    "in_use": bundled is not None and bundled == chosen.path},
    }


def human_bytes(count: int | None) -> str:
    """Sizes as a package manager writes them, for the one row that needs it."""
    if not count:
        return ""
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f} GB"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.0f} MB"
    if count >= 1_000:
        return f"{count / 1_000:.0f} kB"
    return f"{count} bytes"
