---
id: ISSUE-047
title: Build and repo
status: in-progress
type: feature
area: packaging
created: 2026-08-27
updated: 2026-09-11
related: [ISSUE-044, ISSUE-043]
---

## Summary

Create process for build packaging, and a rpm repo

## Acceptance / done-when

- A wheel builds and installs, and the app runs from the installed package.
- The rpm builds and `dnf install` resolves every dependency, including the SDK,
  against real repositories.
- The deb builds on current Debian and Ubuntu.
- One command sets the version everywhere, and CI fails a tag that disagrees
  with the tree.
- The COPR project exists and rebuilds on push.
- Repo instructions for rpm and for deb.

## Notes & worklog

- ensure we can support debian and gnome
- ensure version gets updated in the app if we tag a version number in git
- create build workflow for rpm and deb
- add custom repo instructions for both rpm and deb type distros
- 2026-09-11: the flat layout did not build at all. Automatic discovery offered
  to package `issues/`, `skills/` and `packaging/`, then refused with "Multiple
  top-level packages discovered". Installing it would have put 25 modules named
  `config`, `session`, `files`, `db`, `events`, `version` and so on into
  site-packages. Moved into a `codinian/` package; `pyyaml`, which `issues.py`
  has always imported, was also missing from the dependencies.
- The claude-agent-sdk wheel on PyPI is 317 MB: it bundles a copy of the `claude`
  binary at `claude_agent_sdk/_bundled/claude`, which is why it is tagged
  manylinux rather than any. The sdist is 345 KB and carries no binary, and
  `_find_cli` falls back to `claude` on PATH, so the COPR package is built from
  the sdist. Not ours to redistribute either way.
- Fedora 44 covers everything else, including python3-mcp 1.26.0 and
  python3-aiohttp 3.13.5.
- Debian is blocked on aiohttp, not on packaging. Measured in containers:
  trixie 3.11.16, Ubuntu 25.10 3.11.16, Ubuntu 24.04 3.9.1, sid 3.14.1, against
  a 3.12.14 floor. Debian's security tracker lists trixie's build as vulnerable
  to CVE-2025-53643, no-DSA. The deb keeps the floor and refuses to install
  rather than serving over a proxied tailnet with that parser.
- Built and verified locally rather than in CI: the rpm (rpmbuild), the deb
  (podman, debian:sid, installs cleanly), the wheel, the COPR make target, the
  desktop entry and the AppStream metadata.
- Still to do, none of it in this repo: create the COPR project and its webhook;
  an apt repo needs a signing key and hosting; the AppStream metadata has no
  screenshot, which GNOME Software will show as a thin listing. ISSUE-043 is
  still open, so the "0.2.0 when the tests and container work land" line in the
  original acceptance is half met.

## Resolution


