---
id: ISSUE-041
title: Security hardening follow-ups from the pre-public review
status: done
type: chore
area: remote
created: 2026-08-21
updated: 2026-08-24
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

## Resolution

All four, none of them large.

**aiohttp is floored at 3.12.14** in `pyproject.toml`, with the reason in a
comment above the line: that is the release which fixed CVE-2025-53643, request
smuggling through trailer-section parsing in the pure-Python HTTP parser. The
dependency carries the remote server, so it faces whatever reaches the bind
address; leaving it unbounded meant it resolved to whatever happened to be
installed.

**`vcs.create_tag` passes `--`.** `["tag", "-a", name, "-m", message]` became
`["tag", "-a", "-m", message, "--", name]`, and the lightweight branch became
`["tag", "--", name]`. Checked that git accepts the separator in both forms
before changing anything, since `git tag` is not `git commit` and the manual
does not spell it out: in a scratch repo, `git tag -a -m msg -- v1` and
`git tag -- v2` both create the tag.

**Image URLs are allowlisted.** `resultImages` in `remote/static/app.js` now
runs `source.url` through `isSafeImageUrl`, which accepts `https:` and
`data:image/` and nothing else. The old code put whatever a tool result named
straight into `img src`, so a tool result could drive an outbound fetch to any
scheme the browser would honour.

**The origin check is documented as the second lock, not the boundary.** Said
in two places, because two audiences read it: the docstring on `_same_origin`
for anyone changing that code, and a paragraph under the HTTP API table in
`docs/remote-access.md` for anyone deciding how to expose the port. Both say the
same thing, that `Origin` and `Host` come from the caller and stay consistent
under DNS rebinding, so the token is what actually protects a state-changing
route.
