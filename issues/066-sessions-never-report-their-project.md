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

- **`project_id` is not the only null, which is the best lead in here.** The
  same three sessions report `sdk_session_id: null` from the API while the
  database has an id for every one of them:

  ```
  id         api sdk_session_id   db sdk_session_id
  bf80db73   None                 e680c189-54a0-4d43-b30e-56662d299f0e
  64821ea5   None                 c46cb6c1-7571-4f7b-9cf0-11bec84d1c80
  f2664af9   None                 6965a686-8ab2-4631-b4c2-eae54ae2bbdc
  ```

  That is contradictory on its face. `SessionManager.set_sdk_session_id` writes
  `self._sessions[id].sdk_session_id` and `Session.meta()` reads
  `self.sdk_session_id`: the same attribute on the same object. The database row
  is written by `_persist` from that same object. So the object the API reads
  and the object the database was written from disagree about a field neither
  path computes.

  Two fields, both null, both on `meta()`, one of them provably non-null at the
  moment it was persisted. Whatever explains that explains `project_id` too, and
  it is more likely to be about **which objects the manager is serving** than
  about the project resolver, which works in isolation.

- **Start here tomorrow: restart the app and ask again.** The instance measured
  above had been up since 2026-09-22 11:58 and could not be restarted, because
  work was going on in it. If a fresh instance answers correctly, this is about
  something that happens to `_project_resolver` over a long run rather than
  about the wiring, and the next question is what.

- `window.py:486` reads `session.project_id()` too, so whatever that drives is
  wrong in the same way.

## Resolution
