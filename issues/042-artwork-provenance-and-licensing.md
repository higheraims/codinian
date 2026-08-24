---
id: ISSUE-042
title: Re-source the app mark's stock elements for a clean license
status: done
type: chore
area: gui
created: 2026-08-21
updated: 2026-08-21
related: [ISSUE-021]
---

## Summary

The app mark (`docs/art/codinian.svg`, copied to `remote/static/codinian.svg`)
was assembled from a stock vector pack (a "vintage Roman empire elements"
set with a gladiator sword and an olive branch). The repo is GPL-3.0, and
redistributing stock art without a compatible license is a real risk. The mark
is kept for now, but its bought-in elements -- the olive branch in particular --
should be replaced with original or clearly-licensed equivalents before this is
treated as settled.

## Acceptance / done-when

- Every visual element in the mark is either original work or carries a license
  compatible with redistribution under GPL-3.0, recorded in the repo.
- The olive branch is redrawn or re-sourced.
- `docs/art/` notes the provenance and license of the final mark.

## Notes & worklog

- 2026-08-21
   - Raised during the public-release prep. The mark ships as a single
  SVG referenced at runtime (`window.py` loads `docs/art/codinian.svg`), so it
  cannot simply be dropped without a replacement. 
   - Sourced vectors from openclipart.org and replaced olive wreath.
- 2026-08-24
   - embedded source URL in SVG art to credit the openclipart source for wreath
