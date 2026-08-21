---
id: ISSUE-003
title: TranscriptEvent model and SessionManager event bus
status: done
type: feature
area: sdk
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-002, ISSUE-004, ISSUE-005]
---

## Summary

The backend needs one canonical event type before anything can render a structured
transcript. Today `Session` (session.py, 118 lines) carries a status derived from output
timing, and `SessionManager` is a list with callbacks; neither can express "Claude is
waiting on an approval for tool_use_id X".

Define `TranscriptEvent` covering `text`, `thinking`, `tool_use`, `tool_result`,
`approval_request`, `approval_resolved`, `usage`/`result`, and `system`/`status`. Give
`Session` a `kind` field (`sdk` or `terminal`), an ordered event list, and a status
derived from those events rather than from how long output has been quiet. Turn
`SessionManager` into the hub that appends an event to a session and broadcasts it to
subscribers: WebSocket clients, the embedded WebKitGTK pane, and the GTK sidebar.

The running/idle/stuck heuristic in the sidebar goes away for `sdk` sessions, because the
SDK reports thinking, calling a tool, waiting on approval, and done explicitly. Terminal
sessions keep the old heuristic, since nothing better exists for them.

## Acceptance / done-when

- `TranscriptEvent` is defined once, serializes to JSON, and both the WebSocket feed and
  the DB use that one shape.
- `Session` has `kind`, an event list, and an event-derived status; terminal sessions still
  get their timing-based status.
- `SessionManager` broadcasts appends to any number of subscribers, and the GTK sidebar is
  one of them (via `GLib.idle_add`, since events arrive off the main thread).
- Existing VTE terminal sessions still start, run, and show status.

## Notes & worklog

- Blocked in practice on [[ISSUE-002]]: the event shapes should follow what the SDK
  actually emits, not what we guess it emits.
- The status column already persists to SQLite (db.py). Decide whether events persist too,
  or whether the JSONL transcript is the durable record and events are in-memory. That
  choice also decides how much [[ISSUE-009]] has to reconstruct.

## Resolution

2026-08-19. `events.py` defines `TranscriptEvent` (a `type` + common fields + a `data`
dict that flattens to the wire shapes in `docs/transcript-protocol.md`) and the
`SessionStatus` enum. `Session` gained `kind`, `permission_mode`, `sdk_session_id`,
`sdk_status`, `cost_usd`, a `wire_status()` that unifies both kinds onto the protocol
vocabulary, and a `meta()` for SessionMeta. `SessionManager` gained the event bus:
`add_event` assigns a per-session `seq` under the lock and broadcasts to subscribers
outside it, plus `get_events`, `subscribe_events`, and `sessions_meta`. The GTK sidebar
subscribes and updates sdk dots via `GLib.idle_add`; terminal sessions keep the timing
heuristic (the health loop skips sdk kinds).

**Durability decision:** events are in-memory per session for M1. The SDK's own JSONL
transcript is the durable record; a late subscriber gets the full backlog via the
`snapshot` message, and [[ISSUE-009]] reconstructs history from JSONL. No DB schema change.

Verified by `test_ws_integration.py`: nine event types streamed in order with no seq
gaps. Existing VTE terminal sessions are untouched (the terminal build path and inject
queue are unchanged).
