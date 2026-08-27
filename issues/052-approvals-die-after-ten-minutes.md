---
id: ISSUE-052
title: An approval left for ten minutes dies, and the card stays clickable
status: done
type: bug
area: sdk
created: 2026-08-27
updated: 2026-08-27
related: [ISSUE-008, ISSUE-050]
---

## Summary

Claude asked four questions in an Advent-Research session. The user was away
from the desk. When they came back they read the questions, made four
selections, clicked Answer, and got a red banner: "That approval is no longer
pending. It may have been answered from another client, or the session may have
restarted." None of those was what happened. The request had been dead for
minutes, and nothing on screen said so.

The original report, verbatim:

> There's a timeout at all on a question whose answer comes from a human.
> Waiting is the normal case.
>
> The UI stays fully interactive after the request is dead. It let you read four
> questions, make four selections, and click Answer before telling you the
> request had expired. The panel should mark itself expired the moment the
> handler gives up, so you don't spend the effort.

### Where the timeout is

Not in Codinian, and not in `can_use_tool`. It is the bundled CLI's budget for
an SDK `PreToolUse` hook callback, and it is 600 seconds. From that session's
transcript (`f34ef2ef-a435-41de-896b-1bef5128b130.jsonl`):

| event | time | gap |
|---|---|---|
| `AskUserQuestion` tool_use | 19:02:40.406 | |
| tool_result, `is_error: true` | 19:12:40.415 | 600.009 s |
| retry tool_use | 19:12:52.600 | |
| tool_result, `is_error: true` | 19:22:52.605 | 600.005 s |

Both errors read "PreToolUse hook did not respond before its timeout (host
client may be unreachable). The tool call was not executed." Four measurements
against the bundled CLI, each reproduced with a hook that sleeps:

| what | result |
|---|---|
| hook with no `timeout`, sleeping 600 s | cancelled at 600.0 s after entry |
| hook with no `timeout`, sleeping 75 s | ran to completion |
| `HookMatcher(timeout=1800)`, sleeping 660 s | ran to completion, so the field is seconds and it replaces the default |
| `can_use_tool` sleeping 700 s | ran to completion, so the permission callback has no such budget |

`_ask_question` and `_ask_user` both wait inside that hook, so this is not
specific to questions. Any tool approval left for eleven minutes comes back to
the model as an error saying the host client may be unreachable.

### Why the card outlived the request

The CLI abandons the callback by sending `control_cancel_request`; the SDK
cancels the task running it; cancelling the task that awaits `fut` cancels
`fut`. Neither `_ask_question` nor `_ask_user` catches that, so:

- `_pending` and `_pending_meta` keep the entry. `pending_approvals()` reports a
  dead request for the rest of the session and the cross-session inbox lists a
  row nobody can act on.
- No event is emitted and the status stays `awaiting_approval`, so the session
  claims to be waiting for an answer it can no longer take.
- The card stays live. `answer_question` finds `fut.done()` and returns False,
  which the server sends back as `stale_or_unknown_request`, which
  `buildQuestionCard`'s `rejected()` handles by setting `settled = false` and
  re-enabling the buttons. The user can fill it in and be refused repeatedly.

## Acceptance / done-when

- An approval or a question can be answered after sitting for longer than ten
  minutes.
- When a request does die, every client marks its card expired at that moment,
  including one that connects afterwards and replays the transcript.
- The session leaves `awaiting_approval` and its inbox row goes.
- An expired question keeps the selections on screen and offers them back,
  rather than making the user retype what they already chose.

## Notes & worklog

- The `tool_use_id` is identical across the `PreToolUse` hook, `can_use_tool`
  and the `PostToolUse` hook for one `AskUserQuestion` call (verified), so the
  wait can move to the permission callback and `_post_tool_use` still finds the
  stashed answer under the same key.
- Moving ordinary approvals to `can_use_tool` as well was considered and
  rejected. The hook is what stops an `allowed_tools` entry or an allow rule in
  `~/.claude` from auto-approving a call before the callback is consulted, which
  is the reason [[ISSUE-002]] chose the hook in the first place.
- `bypassPermissions` was the mode worth checking before moving the question,
  since a mode that skips permission checks could have skipped the callback the
  question now waits in. It does not: `can_use_tool` is still invoked for
  `AskUserQuestion` in that mode, so a question still reaches the user in every
  mode, which is what [[ISSUE-050]] settled.
- `question_request` and `question_resolved` were missing from `EVENT_TYPES`,
  left out when [[ISSUE-050]] added them to the wire. Added here rather than
  filed separately: the set is one line and it claims to list the wire's types.

## Resolution

Four changes, one per defect in the report plus the one the report implied.

**The ceiling.** `HOOK_TIMEOUT_SECONDS`, passed to the `PreToolUse` matcher, is
a day instead of the CLI's ten minutes. Verified: `timeout=86400` with a hook
that slept 610 seconds ran to completion and its tool call went through, so the
value replaces the default rather than being capped by it.

**The question's wait moved to `can_use_tool`**, which has no budget of its own.
The hook now allows `AskUserQuestion` immediately, `_can_use_tool` asks, and
`_post_tool_use` finds the answer under the same `tool_use_id` it always did.

**`_wait_for_answer`** wraps both waits, so a cancelled callback pops its
pending entry, emits `approval_expired`, and puts the session back to `working`.
That last part is also what withdraws the desktop "needs approval" notification,
which keys off the status and used to outlive the approval.

**The card retires itself**: dashed border, disabled buttons, a line saying what
expiring cost, and the selections left on screen. A question that had answers in
it grows a "Put my answers in the composer" button. `stale_or_unknown_request`
now routes to the same terminal state instead of handing the buttons back.

Checked against a real session, driven through the served page in a headless
browser, with the server and a real `claude` process running from a scratch
config on port 8799 so the instance already open was untouched:

- A question sat for 72 seconds, was answered from the page, and the model
  replied "You chose tea and mornings."
- Stop with a `Bash` approval pending: `approval_expired` at +111 ms, status
  back to `working` then `awaiting_input`, all three buttons disabled, the card
  reading "Expired. The tool call did not run."
- Stop with a question pending and one option chosen: the card went dashed with
  the choice still highlighted, and the recovery button put "Which colour do you
  prefer? / Red" in the composer.

Interrupt turns out to be the everyday trigger for the expiry path, not the
timeout: it cancels a pending callback within milliseconds. Before this, hitting
Stop with an approval on screen left the same zombie card and the same stuck
`awaiting_approval` status as the ten-minute timeout did.
