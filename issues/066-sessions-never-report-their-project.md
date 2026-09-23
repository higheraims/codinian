---
id: ISSUE-066
title: Every live session reports no project, so no project lists its own sessions
status: open
type: bug
area: tools
created: 2026-09-23
updated: 2026-09-23
related: [ISSUE-020, ISSUE-063]
---

## Summary

`Session.meta()` returns `project_id: null` for every live session, whatever
folder it is running in. `get_project` (`remote/projects_api.py:157`) filters
running sessions with `m.get("project_id") == pid`, so every project's Sessions
tab says "No sessions running in this project" and always will.

Asked of the running instance on 2026-09-23:

```
bf80db73  project_id=None  /home/ntyler/Projects/MyCal
64821ea5  project_id=None  /home/ntyler/Projects/qicasa
f2664af9  project_id=None  /home/ntyler/Projects/codinian
nehemiah project page reports running: 0
```

The third is running in this repo, which is registered as `d134a760`.

It also half-undoes [[ISSUE-063]]. The New session button creates the session,
then the page it was pressed on never shows it.

## Acceptance / done-when

- A session running inside a registered folder reports that project's id.
- A project's Sessions tab lists the sessions running in it.
- Whatever made this fail silently cannot fail silently again: `project_id` is
  the only field in `meta()` whose absence looks exactly like an empty list.

## Notes & worklog

- 2026-09-23: **The cause is not pinned down**, which is the whole reason this
  is a ticket rather than a commit. Everything that should break is intact:

  - The resolver is right in isolation. A fresh process:
    `/home/ntyler/Projects/codinian -> 'd134a760'`,
    `/home/ntyler/Projects/MyCal -> 'd429b43e'`, 14 registry entries.
  - `main.py:92` installs it in `_on_activate`, before the window is built.
    That line has been there since d05a110 on 2026-09-11, so the instance
    showing the fault started long after it landed.
  - There is no second copy of the package shadowing the repo; `find_spec`
    outside the working tree finds nothing.
  - `sessions_meta()` builds from live `Session` objects, so `meta()` and
    therefore `project_id()` do run. The key is present in the response with a
    null value, which rules out the field being dropped somewhere later.

- **Start here tomorrow: restart the app and ask again.** The instance measured
  above had been up since 2026-09-22 11:58 and could not be restarted, because
  work was going on in it. If a fresh instance answers correctly, this is about
  something that happens to `_project_resolver` over a long run rather than
  about the wiring, and the next question is what.

- `window.py:486` reads `session.project_id()` too, so whatever that drives is
  wrong in the same way.

## Resolution
