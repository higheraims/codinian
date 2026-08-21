---
id: ISSUE-019
title: Support standardized issues system
status: done
type: feature
area: tools
created: 2026-08-17
updated: 2026-08-20
related: [ISSUE-020, ISSUE-023]
---

## Summary

While developing an earlier project we came up with an issues system that works locally, because GitHub issues was not what we wanted. Issues is a way to track dev tasks and bugs and document their status. It uses simple .md format and was originally made to work with the FrontMatter VSCode plugin. We will mature and standardize this system so we can use it on any project.

## Notes & worklog

- What we want to do is standardize the Issues systerm.
- We will standardize on a few points
    - The issues system is a per-repo system
    - It will live in the repo's root folder as a subfolder called /issues
    - once we have the feature set built out we will not depend on FrontMatter
    - codinian will store any relevant settings in a per-repo file (potentially useful for other features as well)
- Issues will also be flexible, because some tags will be repo-specific
    - one project may have categories such as engine and ui, while another may have db and networking
- Features will include
    - an Issues view for a project (this relates to ISSUE-020 - project view doesn't exist yet)
    - issue list, filterable and sortable
    - ability to create and edit issues in-app (can open in a tab if that makes sense)
    - since we are standardizing the format, we can expose the front matter as editable fields rather than just text
    - A simple WYSIWYG editor for the main .md body of the issue
    - Body can also contain different sections - worth thinking through - we have at least summary and notes - those could also be exposed as separate editable fields
- Migration
    - once we have standardized and built the features we want, we'll optionally migrate all existing issues systems across repos in our /Projects folder. This will require a one-off processing step since the features have drifted a little bit with each iteration as we have borrowed the system out of that project into others - file naming, front matter conventions and the folder location relative to project root can differ between projects. We'll need to update those, resolve any changes in file references scattered throughout, and in some cases commit the changes in git with appropriate comments.

## Resolution

Built in M4. The system is `.codinian/settings.json` in the repo for the
per-repo configuration, `issues.py` for the file format, the `/api/projects/…
/issues` routes for access, and the Issues tab of `project.html` for the
interface.

The parts that landed as asked: per-repo, in-repo, no FrontMatter dependency,
repo-specific `statuses`/`types`/`areas` that the client builds its filter chips
and dropdowns from rather than hard-coding, a filterable and sortable list,
create and edit in-app, frontmatter as real form controls, and each body section
as its own editable field.

Two things worth knowing rather than discovering later:

- **The body editor is markdown with a formatting toolbar and a live preview,
  not a true WYSIWYG surface.** A contenteditable that round-trips to clean
  Markdown is a much larger and more fragile piece of work than it looks, and
  the failure mode is silent corruption of a file the tracker exists to keep.
  If the editing experience turns out to matter more than the risk, that is its
  own issue.
- **The issues directory is discovered, not fixed.** Settings first, then
  `issues/`, then `docs/issues/`. The standard is repo-root `issues/`, but this
  repo's own tracker still lives in `docs/issues/` and works untouched.

The cross-repo migration this issue also described is [[ISSUE-023]]; it was
always a separate step to take after the features existed.
