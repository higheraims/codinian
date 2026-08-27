---
id: ISSUE-049
title: Plan mode asks approval for every read-only command
status: done
type: bug
area: sdk
created: 2026-08-27
updated: 2026-08-27
related: [ISSUE-008, ISSUE-027]
---

## Summary

A planning session put an approval card in front of every `cat`, `grep`, `sed`
and `ls`. Dozens of them in one session, all read-only, none worth a decision.
The session was reported as "almost unusable".

Codinian gates tool calls through a PreToolUse hook rather than `can_use_tool`,
deliberately, so the hook fires even where the CLI's own allow rules would
auto-approve. `MODE_DECIDES_ITSELF` listed only `auto` and `dontAsk`, so in
plan mode `_auto_decision` returned None for every tool and each one became a
card. The hook was intercepting calls the CLI would have waved through.

## Acceptance / done-when

- A read-only command in plan mode runs without an approval card.
- Plan mode still refuses edits.
- Leaving plan mode still asks.

## Notes & worklog

- Measured against a hook mirroring this one, three read-only commands in plan
  mode: three cards before, none after. The commands ran either way.
- Checked the safety side in the same harness. Asked a deferred plan-mode
  session to write two files; the `Write` was redirected to the plan file and
  nothing reached the target directory. The CLI refuses edits in plan mode
  without consulting the client at all, which is why deferring is safe.
- `ExitPlanMode` is held back on the ask path through the new `ALWAYS_ASK` set.
  Deferring it would hand the plan to the CLI expecting a client that approves
  plans; Codinian is not one, so the turn would park on a question nobody is
  shown, which is the ISSUE-008 hazard.
- `ALWAYS_ASK` is consulted inside the `MODE_DECIDES_ITSELF` branch rather than
  at the top of `_auto_decision`. Its reason is that deferring parks the turn,
  which says nothing about `bypassPermissions` or `acceptEdits`, and those two
  answer rather than defer.

## Resolution

`plan` joins `auto` and `dontAsk` in `MODE_DECIDES_ITSELF`, so plan-mode calls
fall through to the CLI, which auto-approves read-only Bash and refuses edits.
`ExitPlanMode` is exempt and still reaches the user.

The general shape is worth keeping in mind for any future mode: the hook should
only ask about what the CLI would otherwise ask about. Where the CLI already has
a stricter answer than a card can express, a card is noise.
