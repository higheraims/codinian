---
id: ISSUE-075
title: A slash command replays as a typed turn, and origin alone was the wrong marker
status: done
type: bug
area: sdk
created: 2026-09-24
updated: 2026-09-24
related: [ISSUE-074]
---

## Summary

Two faults in the same function, one left over from [[ISSUE-074]] and one that
ticket recorded as outstanding.

A slash command writes two records: an `isMeta` caveat, then the
`<command-name>` envelope as its child. The caveat is dropped before replay,
the envelope is not, so `/model` came back as a bubble reading
`<command-name>/model</command-name>`.

The worse one: [[ISSUE-074]] folded any user turn carrying an `origin` record.
The CLI stamps a typed message `origin: {"kind": "human"}`, so that hid real
messages. Across the transcripts on this machine it would have hidden 21 of
them, several written in the session that found it.

## Steps to reproduce

Run `/model` in a session, quit, reopen it. For the second fault, type any
message in a recent CLI version and reopen.

Classifying every user turn in the 151 transcripts on this machine:

```
                                     before       after
prose wrongly folded                     21           0
envelopes still drawn as typed           14           0
```

## Acceptance / done-when

- A slash command does not come back as a bubble.
- Every message the user typed still does.
- Checked against real transcripts, not only constructed ones.

## Notes & worklog

- 2026-09-24: `origin.kind` is what decides, not whether `origin` is there.
  Absent on 638 prose turns and on all 14 command envelopes, so it cannot carry
  the command case on its own.

- The command envelope is found by its parent: it descends from a record
  dropped as `isMeta`. That marked all 14 envelopes and none of the 659 real
  turns. The parent is always the earlier record in file order, 14 times out of
  14, so collecting meta uuids while reading is enough and no second pass is
  needed.

- `promptSource` was tried first and rejected: `None` on all 14 envelopes but
  also on 27 typed turns.

- The first survey behind [[ISSUE-074]] read `origin` as a flag because every
  value it saw was `task-notification`. `{"kind": "human"}` only appears on
  turns typed through a recent CLI, and the session doing the surveying was
  writing them while it ran. Counting the false positives, rather than the
  hits, is what caught it.

## Resolution

`_user_source` returns `operator` for `origin.kind == "human"`, `injected` for
any other `kind`, and otherwise falls through to the parentage check.
`_events_from` collects the uuids it drops as meta and hands them down.

Seven tests over the two functions. Reverting the `kind` check fails the typed
turn; reverting the parentage check fails the command envelope.
