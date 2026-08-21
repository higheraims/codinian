---
id: ISSUE-002
title: Confirm Agent SDK auth and event mapping (M0 spike)
status: done
type: spike
area: sdk
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-003, ISSUE-004, ISSUE-009, ISSUE-011]
---

## Summary

Two unknowns gate the whole pivot described in [../pivot-plan.md](../pivot-plan.md).
Settle both before any UI work, because either one can change the architecture.

1. **Auth.** Does `claude-agent-sdk`, installed in a venv, authenticate against the
   existing Claude subscription login, or does it demand an API key? If it demands a key,
   the account and billing story for this app changes, and so does what we can ship.
2. **Event mapping.** Does the message stream map cleanly onto the `TranscriptEvent`
   shapes we plan to render, and does the blocking `canUseTool` approval model behave the
   way the docs describe?

There is a third environmental question folded in here: codinian has to run where the
node and `claude` binaries the SDK subprocess spawns are reachable. On this machine that
means the host, not the flatpak/toolbox container. Confirm which, and record it.

## Acceptance / done-when

- `claude-agent-sdk` installed in a venv, and a one-line query answered without an API key
  (or the failure documented, with what it demands instead).
- A headless script drives `ClaudeSDKClient` in a working directory, streams events, and
  prints each mapped `TranscriptEvent` as JSON.
- That script exercises `canUseTool` with a manual approve and a manual deny, and both
  outcomes reach the model.
- The findings are written back into the pivot plan or into [[ISSUE-003]], whichever the
  answer affects.

## Notes & worklog

- The pivot plan already records one correction worth confirming in the spike: SDK
  `resume` continues a conversation but does not re-emit past messages, so a resumed pane
  opens empty. That is what [[ISSUE-009]] exists for.
- Ran 2026-08-19 on the host (node v22.23.1, `claude` 2.1.224, python 3.14.7,
  `claude-agent-sdk` 0.2.140) in a throwaway venv under the scratchpad, touching no project
  files or running processes. Two scripts: one streamed a real turn and one proved the
  approval gate.

## Resolution

Both gating unknowns are settled. The pivot proceeds on the Python SDK as planned.

**Auth (unknown 1): reuses the existing subscription login.** With `ANTHROPIC_API_KEY`
unset, a full turn ran against the local `~/.claude` credentials. The init event reported
`apiKeySource: "none"` and `model: "claude-opus-5"`, and the run completed with a
`ResultMessage`. No API key or separate billing setup is needed.

**Event mapping (unknown 2): clean.** `system`/init, `text`, `tool_use`, `tool_result`, and
`result` all map onto the planned `TranscriptEvent` shapes. Two things to fold into
[[ISSUE-003]]: a `RateLimitEvent` type also arrives on the stream (carries rate-limit
status worth surfacing in the GUI), and `ResultMessage` already carries `total_cost_usd`
plus detailed token `usage`, so [[ISSUE-011]] is fed for free.

**Approval architecture (the finding that shapes [[ISSUE-004]]).** `can_use_tool` is
authoritative only when nothing pre-approves the call. Two things shadow it and silently
auto-approve: any whole-tool entry in `allowed_tools`, and allow rules from the user's
`~/.claude` settings files. Passing `setting_sources=[]` (the field takes any of
`user`, `project`, `local`) loads no such rules; with that set and no `allowed_tools`,
every tool falls through to the callback. Proven: the first `Write` was denied
(`is_error: true`, file not written) and the retry was allowed (the tool executed, failing
only on a real OS permission error from the path the model picked). The callback also
supports the full inline-approval UX: `PermissionResultAllow(updated_input=...)` for
edit-and-approve, and `PermissionResultDeny(message, interrupt)` for a denial reason.

**Open design question for [[ISSUE-004]] (not a blocker).** `setting_sources=[]` also drops
the project's `CLAUDE.md`, skills, and settings context, which a good GUI session probably
wants. The SDK warning points at the alternative: a `PreToolUse` hook gates every call even
when allow rules exist. So M1 chooses between (a) `can_use_tool` with no setting sources
(authoritative approvals, no project context) and (b) a `PreToolUse` hook (keeps project
context and still gates). Option (b) looks preferable; confirm a hook can block before
committing.

**Bonus for later milestones.** The SDK exports session-history APIs (`list_sessions`,
`get_session_messages`, `project_key_for_directory`) that may let [[ISSUE-009]] read history
without hand-parsing JSONL, and `get_subagent_messages` / `list_subagents` that feed
subagent visualization ([[ISSUE-017]]). The options surface also already has `resume`,
`session_id`, `fork_session`, `session_store`, `disallowed_tools`, `add_dirs`,
`max_budget_usd`, `agents`, `skills`, and `sandbox` for downstream work.
