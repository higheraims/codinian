---
id: ISSUE-050
title: AskUserQuestion is missing from every session
status: done
type: feature
area: sdk
created: 2026-08-27
updated: 2026-08-27
related: [ISSUE-010, ISSUE-027, ISSUE-049]
---

## Summary

Claude could never ask the user a question in Codinian. The AskUserQuestion tool
was absent from every session, so a model that wanted to offer three options had
to guess one instead, or write the question as prose nobody could answer
structurally.

The cause was one missing option. The CLI registers AskUserQuestion only when
the client supplies a `can_use_tool` callback, and this app gates on a PreToolUse
hook and passed no callback. Measured as a 2x2 against the bundled CLI:

| SDK options | AskUserQuestion offered |
|---|---|
| `can_use_tool` only | yes |
| hooks only (what Codinian did) | no |
| hooks and `can_use_tool` | yes |
| neither | no |

## Acceptance / done-when

- The model can call AskUserQuestion, and the question reaches the user.
- The answer reaches the model as an ordinary result, not an error.
- A normal tool call is still put to the user exactly once.

## Notes & worklog

- Three wrong theories died on the way here, each from reading rather than
  running. The `system/init` message's `tools` array does not list every
  callable tool, so its omission of AskUserQuestion proved nothing; ExitPlanMode
  was absent from the same array and callable anyway. The TS SDK's
  `askUserQuestion` option turned out to be
  `toolConfig.askUserQuestion.previewFormat`, markdown versus HTML for option
  previews, not an answer channel. And the Python SDK is not behind the TS SDK
  here: neither exposes a way to answer, and 0.2.145 changes nothing.
- Answering through the permission callback's deny message does work, and the
  CLI's own prompt treats a `[User answered AskUserQuestion]:` prefix as user
  intent, but it lands as `is_error: true`. Rejected for that reason.
- The route taken instead: allow the call, then replace its output with a
  PostToolUse hook returning `updatedToolOutput`. The CLI validates the
  replacement against AskUserQuestionOutput and keeps the original on a
  mismatch, so the payload is shaped `{questions, answers, response?}`.
- The question is caught in the PreToolUse hook rather than the permission
  callback. The hook runs first, so catching it later meant the ordinary
  approval card got there before the question card did. Caught before the
  permission mode is consulted, too: a question is not a permission, and no
  mode should answer it for the user.
- Adding `can_use_tool` also gives the CLI somewhere to send the calls it
  decides to ask about in the modes where the hook defers. Those previously had
  nowhere to go.

## Resolution

`can_use_tool` is supplied, the PreToolUse hook catches AskUserQuestion and puts
it to the user as a `question_request`, and a PostToolUse hook puts the answer
in the tool's output. The model reads an ordinary successful result.

The card is blue rather than the approval card's amber, because amber is this
app's colour for "something is waiting on your consent" and a question waits on
your knowledge. It offers the model's options plus a freeform box, and Skip
rather than Deny.

A pending question also reaches the cross-session inbox, as a row that opens the
session. The picker stays in the transcript: a second copy of it in a panel that
narrow would be worse than a short walk.
