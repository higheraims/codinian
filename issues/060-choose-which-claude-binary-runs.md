---
id: ISSUE-060
title: Sessions ran the stale CLI inside the SDK, with no way to see or change it
status: done
type: feature
area: sdk
created: 2026-09-21
updated: 2026-09-21
related: [ISSUE-047, ISSUE-050]
---

## Summary

The SDK's `_find_cli` tries the copy inside the wheel first, and that copy never
moves: `claude-agent-sdk` 0.2.140 was published on 18 August 2026 carrying CLI
2.1.235 and will carry 2.1.235 for as long as it exists. On this machine dnf had
`/usr/bin/claude` on 2.1.267 at the same time, from Anthropic's own repository.
Codinian ran the older one, and the only way to find that out was to read the
process tree.

`ClaudeAgentOptions(cli_path=...)` is documented, the bundled binary is the
default rather than a requirement, and the SDK's one version constraint is a
floor it warns about rather than enforces:

    MINIMUM_CLAUDE_CODE_VERSION = "2.0.0"

Measured before changing anything: SDK 0.2.140 driving 2.1.267 gave PreToolUse
and PostToolUse hooks firing, a Bash call with `is_error: False`, 17 stream
events, `get_server_info` answering with 53 commands, a clean
`set_permission_mode` round trip, and the ISSUE-050 AskUserQuestion 2x2
replicating identically. Tool counts with and without `can_use_tool` were 29 and
26 on both CLIs. It is a drop-in.

## Acceptance / done-when

- A setting chooses the binary: this machine's install, the copy inside the SDK,
  or a path. Default is the system install, falling back to the bundled copy.
- A path the user typed has no fallback. Silently running something else is how
  someone ends up debugging a binary they thought they had ruled out.
- The About tab says which binary is running and what version it is, lists both
  copies, and gives the size of the bundled one.
- The search looks where the SDK's own `_find_cli` looks, including its Windows
  rules, so the two cannot disagree about where a CLI lives.

## Notes & worklog

Done. `claude_cli.py` resolves the path and `sdk_session.start` passes it as
`cli_path`; nothing found means no keyword at all, which leaves the SDK to
search and to raise its own not-found error, the one that names the installer
for the platform.

The bundled version is read out of the SDK's `_cli_version.py` rather than by
running the binary, and `version()` caches on the file's mtime and size rather
than its name, so a dnf upgrade under a running app is picked up. An upgrade
mid-session is safe either way: the running process holds the old inode, and
new sessions get the new binary with no restart.

Not done here, and not needed: nothing prompts to update the SDK. The Python
half is already imported by the time anyone could act on it, so applying an
update means a restart, and a channel that moves monthly is the wrong one to
build a prompt around when the CLI moves several times a week.

On Windows a `claude.cmd` from npm is rejected rather than handed over as
`cli_path`: the SDK refuses to spawn a batch file, since Windows runs one
through `cmd.exe`, which re-parses the command line and expands `%VAR%` inside
quotes. Leaving it unresolved gets the SDK's own refusal, which names the
native installer. That is all Windows needs from this module; whether the app
runs there at all is ISSUE-061.
