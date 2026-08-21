---
id: ISSUE-027
title: Auto mode switch not working
status: done
type: bug
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-004, ISSUE-012, ISSUE-025]
---

## Summary

Auto mode switch (trying to deal with inability to see approvals as a result of the bug described in ISSUE-025) did not take effect, even after the next tool request (bash multiple times and it still was in an approve mode)

## Acceptance / done-when

- Switching to a mode that does not ask stops the approval cards, from the next tool call on.
- The transcript says which mode let a call through, so a session that has gone quiet is legible rather than mysterious.

## Notes & worklog

- See screenshots in Docs folder

## Resolution

The switch worked. Everything downstream of it ignored the result.

`set_permission_mode` reached the CLI, the session metadata updated, and the
transcript printed "Permission mode changed to Auto" -- the second screenshot
shows it, followed by four more Bash approvals. The reason is the choice made
back in [[ISSUE-004]]: approvals run through a `PreToolUse` hook rather than
`can_use_tool`, so the project's CLAUDE.md and skills stay loaded. A hook runs
ahead of the CLI's own permission handling and its `permissionDecision` is
final. `_pre_tool_use` never looked at the mode, so it raised an approval for
every call and every mode behaved like `default`. The SDK says as much in
`_get_can_use_tool_shadowed_warning`, from the other direction: use a hook when
you want to gate every call regardless of mode.

The hook now reads the session's mode first, in `_auto_decision`:

- `bypassPermissions` approves the call in the hook.
- `acceptEdits` approves `Edit`, `MultiEdit`, `Write` and `NotebookEdit`, and
  still asks about everything else. That is the difference between it and
  `bypassPermissions`.
- `auto` and `dontAsk` return no `hookSpecificOutput` at all, so the call falls
  through to the CLI. Neither can be reproduced here: `auto` runs a model
  classifier over the call and `dontAsk` applies the settings files' allow
  rules, and both can deny.
- `default` and `plan` ask, as before.

Every call that skips the human emits a `permission_note` event, which renders
as one line: "Bash left to the CLI by Auto". A session that stops prompting
should say what stopped it, and `defer` in particular means the answer came
from somewhere Codinian cannot see.

Checked with a harness that drives `_pre_tool_use` directly across all six
modes, asserting both the returned decision and the events raised: the four
non-asking cases return without blocking and emit a note with no
`approval_request`, and `default`, `plan` and `acceptEdits`-on-`Bash` still
park on the future waiting for an answer.

The new-session dialog offered four modes while the transcript header offered
six, so picking "Ask on each tool" at creation and switching to Auto later
crossed two different vocabularies. Both now list all six from
`session.PERMISSION_MODES`.
