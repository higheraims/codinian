---
id: ISSUE-041
title: Security hardening follow-ups from the pre-public review
status: open
type: chore
area: remote
created: 2026-08-21
updated: 2026-08-21
related: []
---

## Summary

Defense-in-depth items from the pre-public security review. None is exploitable
as shipped -- token auth still gates every `/api/` route and the WebSocket -- so
they are grouped here rather than treated as blockers.

## Acceptance / done-when

- Pin `aiohttp` to a known-good minimum in `pyproject.toml` rather than leaving
  it unbounded.
- `vcs.create_tag` builds `["tag", "-a", name, "-m", message]` with no `--`
  before `name`, unlike `commit()`. A `name` of `-f` fails with a git usage
  error rather than doing anything, so there is no payoff, but add the `--` to
  match the pattern `commit()` already uses.
- `remote/static/app.js` `resultImages` inserts an image result's `source.url`
  into `<img src>` with no scheme allowlist, unlike the markdown link renderer.
  Not XSS (`javascript:` does not execute in `src`), but it is an unfiltered
  outbound fetch driven by tool-result content; allowlist `https:`/`data:`.
- Document that the `_same_origin` check is defense-in-depth, not the security
  boundary: under DNS rebinding the `Origin` and `Host` headers stay consistent,
  so the token is what actually protects state-changing routes.

## Notes & worklog

- 2026-08-21: Filed from the pre-public review. The one confirmed vulnerability
  from that review (issues.dir path traversal) was fixed under its own commit.
