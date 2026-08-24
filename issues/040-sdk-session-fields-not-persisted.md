---
id: ISSUE-040
title: SDK session fields do not round-trip through the database
status: done
type: bug
area: db
created: 2026-08-21
updated: 2026-08-24
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

## Resolution

The table persists them, which defuses the landmine rather than fencing it off.

`db.py` gained seven columns: `kind`, `name_is_custom`, `permission_mode`,
`sdk_session_id`, `cost_usd`, `tokens` (the four counts as JSON) and
`totals_cover_this_run_only`. `name_is_custom` was not in the issue's list but
belongs to the same class of mistake: a restore that dropped it would let the
CLI's generated title overwrite a name the user chose.

`sdk_status` is deliberately still absent, and `save_session`'s docstring says
why. It is derived from the events of a running turn loop, no turn loop survives
the process, and a stored one would only ever be a stale claim that a session was
working.

The migration is the pattern that was already there for `resume_session_id`,
generalised into `_ADDED_COLUMNS` and applied with `ALTER TABLE` for any column
`PRAGMA table_info` does not report. Every one is nullable or defaulted, so a row
written by an older version reads back with those fields at their dataclass
defaults; `_tokens_from_row` does the same for a `tokens` column that is null or
holds something unparseable.

Verified against a copy of the real database, which has 19 rows and still carries
the long-dropped `goal` column: `open_db` added all seven, `load_sessions` read
the existing rows back as `kind="terminal"`, a written SDK session round-tripped
every field unchanged, and reopening the connection applied no further changes.
