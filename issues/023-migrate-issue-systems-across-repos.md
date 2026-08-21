---
id: ISSUE-023
title: Migrate the drifted issue systems in the other repos
status: done
type: chore
area: tools
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-019]
---

## Summary

Split out of [[ISSUE-019]], which named this as a separate step to take once the
in-app issues system existed. It now does, so this is the remaining work.

The tracker started in another of the author's repos and was copied by hand into each repo that
wanted it. Every copy drifted: file naming, which frontmatter fields exist and
what they are called, and where the folder sits relative to the repo root all
differ. Codinian can now read any of them if pointed at the right directory, but
the differences still have to be reconciled before one set of tools fits all of
them.

## Acceptance / done-when

- Every repo under `~/Projects` that has an issue tracker has been surveyed:
  where the folder is, what the filenames look like, which frontmatter fields
  are used, and which values those fields take.
- Each one has a `.codinian/settings.json` recording its issues directory and
  its own `statuses`, `types` and `areas`, so a repo whose areas are `engine`
  and `ui` keeps them.
- Wherever a repo is moved to the standard repo-root `issues/`, references to
  the old path elsewhere in that repo are updated in the same commit.
- Codinian's own tracker is moved from `docs/issues/` to `issues/`.

## Notes & worklog

- **Issue ids are permanent.** A migration that renumbers anything defeats the
  point of the tracker: a `ISSUE-004` in a code comment has to keep resolving.
  Renaming a file's slug is safe, changing its number is not.
- The per-repo settings file makes the migration optional rather than forced.
  Discovery already falls back to `issues/` then `docs/issues/`, so a repo works
  untouched; standardising is for the benefit of tooling written later.
- Worth doing per repo, in its own commit, rather than as one sweep — a bad
  normalisation is much easier to back out that way.

- **2026-08-20, done across all eight repos.** Surveyed first: 227 issue files,
  every one parsed and compared before anything moved. Two assumptions in the
  summary above turned out to be wrong. The frontmatter fields had **not**
  drifted — every repo already used the same eight, and only one adds a ninth
  (`milestone`), which survives the round trip. What had drifted was the
  directory, the filenames, and the status/type vocabularies.

  The real finding was one this issue did not anticipate: **112 of the 227 files
  would have been silently reformatted the first time anyone edited them in the
  app** — a blank line after each heading, frontmatter reordered, quoting and
  trailing `# open | done | ...` comments dropped. No information was at risk,
  but every edit would have arrived mixed with layout churn. And **two files
  could not be parsed at all**: one opened its title with a quoted phrase, and
  another had `antialiasing: Wine` inside an unquoted one. Both are readable
  now, with their titles byte-identical.

  Two commits per repo, content then structure. That order is load-bearing: a
  commit that rewrites a file and renames it in one step drops git's similarity
  score below the rename threshold, and `git log --follow` is the whole point of
  a tracker whose ids are permanent. Split, every move is R100 and history
  follows.

  Vocabulary was unified rather than preserved, which is a change from what this
  issue originally proposed: one repo's `todo` became `open`, and `task`/`research`
  in two others became `chore`/`spike`. Areas stayed per repo as written
  here, so a Wine-related repo keeps `wine`/`font`/`tooling`. This repo's own
  tracker turned out to have the same disease in miniature: eight issues used
  `area: ui` and nineteen used `area: gui` with nothing distinguishing them, and
  `ui` was not in the vocabulary this repo ships, so its own filter chips could
  not offer it.

  One repo had nine files carrying two values in one field (`type: feature,
  spec`, `area: gui, engine, render`). Reduced to the first value, which is the
  one thing in this migration that discards information; it is in the parent
  commit if it is wanted.

  42 files renamed, 6 trackers moved, 29 reference lines updated, 8 settings
  files written. Verified: no id changed anywhere; all 227 files round-trip
  byte-identically; the only difference a no-op save now makes is the `updated`
  stamp; all 41 markdown links into a tracker resolve; every status, type and
  area value in every file is offerable by a filter chip.

- **What was left alone.** One repo has no commits at all — no branches, empty log,
  everything untracked — so its files were migrated in place and not committed.
  Five issue files across three repos were mid-edit when this ran; they were
  normalised on disk but kept out of every migration commit.

  One of those needed the commit amending to hold that line. `git mv` of
  the directory re-stages files from the working tree, which quietly undid the
  index surgery meant to keep that file's in-flight edit out, and the commit
  message asserting otherwise was the thing that made it worth fixing rather
  than accepting.

- **The `docs/issues/` fallback in `project.resolve_issues_dir` stays.** Nothing
  under `~/Projects` needs it now, but it is what lets the workspace read a repo
  nobody has migrated, which is every repo added from here on.
