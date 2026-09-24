---
id: ISSUE-074
title: A background task reporting back is replayed as something the user typed
status: done
type: bug
area: sdk
created: 2026-09-24
updated: 2026-09-24
related: [ISSUE-026, ISSUE-009]
---

## Summary

Reopening a session drew raw `<task-notification>` XML in the middle of the
transcript, in user bubbles, 1 KB to 15 KB at a time.

`_user_events` assumed every turn the CLI injects for itself carries `isMeta`,
which `events_for_session` drops before replay reaches it, so whatever is left
is what the person typed. A background agent reporting back does not carry
`isMeta`. It is an ordinary user turn carrying `origin`, so it was labelled
`operator` and drawn verbatim.

Live sessions were never affected: `_handle_block` marks every user text block
`injected`, and the page folds those into a collapsed card. Only replay drew
them, which is why it appeared on restart.

## Steps to reproduce

Run a session that spawns background agents, quit, reopen it. One real
transcript through `events_for_session`:

```
before   23 user events, all source='operator'
          21 of them <task-notification> or <command-name> envelopes
after    19 injected, 4 operator
```

The three messages actually typed are the ones without `origin`.

## Acceptance / done-when

- A resumed transcript folds the same turns the live one folded.
- A turn the user typed is still drawn as theirs.

## Notes & worklog

- 2026-09-24: `origin` is the marker rather than `origin.kind ==
  "task-notification"`, so a kind nobody has met yet does not arrive as a wall
  of XML. A scalar `origin` is left alone: a turn must not be hidden because a
  field had a shape this did not expect.

- Still outstanding, smaller: a slash command replays as
  `<command-name>/model</command-name>` in a bubble. It carries no `origin` and
  no `isMeta`, so nothing structural separates it from typed text yet. One
  event in the transcript this was found in, against 21 notifications.

- The 21 envelopes were about 150 KB of document text, which also feeds the
  transcript size behind [[ISSUE-072]]. Not its cause, but a contributor.

## Resolution

`_user_source` labels a user turn `injected` when the entry carries an `origin`
record, and replay agrees with the live path. Four tests: a typed turn, a task
notification, an unknown `origin` kind, and a malformed `origin`.
