---
id: ISSUE-044
title: Automated test suite
status: done
type: feature
area: tools
created: 2026-08-24
updated: 2026-09-11
related: []
---

## Summary

Create automated test suite

## Acceptance / done-when

- `python3 -m pytest` from the repo root runs green, with no network, no
  display and no GTK import.
- A run cannot reach the user's own data. HOME and git's config are repointed
  before any app module is imported, and `tests/test_sandbox.py` fails if that
  stops working.
- Every module that does not import `gi` is covered, plus the server's auth
  middleware.
- The round-trip test `issues.py` names in its own docstring runs over the real
  `issues/` directory, so a file Codinian would rewrite is caught.

## Notes & worklog

- 2026-09-11: 559 tests in 15 files, 3.4s. pytest only; every other import is
  already a runtime dependency. Config lives in `pyproject.toml`
  (`[tool.pytest.ini_options]`, `pythonpath = ["."]`), so there is no
  conftest.py at the repo root and `tests/conftest.py` runs the sandbox first.
- Nothing is mocked where the real thing will do: `test_vcs.py` runs real git
  in temp repositories, `test_server_auth.py` drives a real aiohttp app.
- Two defects fell out of writing them. `update_issue` raised KeyError on front
  matter carrying no `created` or `id`, which reached the API as a 500;
  `043-container-version.md` ended `## Resolution\n` where the writer emits
  `## Resolution\n\n\n`. Both fixed.
- Not covered: the GTK layer (`window.py`, `settings_view.py`, the dialogs),
  the SDK turn loop's live path, and the HTTP routes in `remote/server.py` and
  `remote/projects_api.py` beyond the auth middleware and the JSON encoder.
  There is no CI workflow.

## Resolution

`tests/` holds the suite; run it with `python3 -m pytest`. README.md has a
Tests section covering what it does and does not reach.
