---
id: ISSUE-040
title: SDK session fields do not round-trip through the database
status: open
type: bug
area: db
created: 2026-08-21
updated: 2026-08-21
related: []
---

## Summary

`Session`'s SDK fields -- `kind`, `permission_mode`, `sdk_session_id`,
`cost_usd`, `tokens`, `totals_cover_this_run_only` -- have no columns in the
`sessions` table, and `save_session`/`load_sessions` drop them. This is inert
today: `load_sessions` is only used to read session ids for stale-notification
withdrawal, never to repopulate the `SessionManager` at startup, which is
deliberate (sessions live in memory only).

It is filed as a landmine. If session restore is ever wired up, `load_sessions`
would hand back every restored SDK session as `kind="terminal"` (the dataclass
default), and `add_session` would spawn a VTE terminal plus a raw `claude`
process instead of resuming through the SDK.

## Acceptance / done-when

- Either the table persists the SDK fields, or `load_sessions` is documented and
  guarded so it can never be used for a restore that misclassifies sessions.

## Notes & worklog

- 2026-08-21: Found in the pre-public review. `db.py` schema vs `session.py`
  dataclass. No user-visible effect at present.
