---
id: ISSUE-059
title: A live session drops the id its subagents are known by
status: done
type: bug
area: sdk
created: 2026-09-20
updated: 2026-09-20
related: [ISSUE-017, ISSUE-058]
---

## Summary

An `Agent` call's result carries the id the subagent is known by. Replayed
history reads it (`_subagent_meta`, off `toolUseResult`) and uses it to fetch
the subagent's transcript. A live session dropped it: `_handle_block` built the
`tool_result` event from the content block alone, and the SDK hands the same
record over separately, as `UserMessage.tool_use_result`.

So the two paths disagreed about what an `Agent` call reported, and the live one
had no id for anything to ask for.

## Why the id matters beyond the transcript link

It is also what continues a stopped agent. From the CLI's own `Agent` tool
result, on both the completed and the async-launched branches:

    agentId: <id> (use SendMessage with to: '<id>', summary: '<5-10 word
    recap>' to continue this agent)

That is the recovery path in anthropics/claude-code#94222 for an agent a usage
limit killed: the transcript survives, and `SendMessage` to the id picks it up
with its original instructions.

## What this does not reach

The id is in the *result*, and a subagent killed mid-flight produces no result.
The CLI's `mapToolResultToToolResultBlockParam` has branches for
`teammate_spawned`, `remote_launched`, `async_launched` and `completed`, and
throws on anything else -- there is no killed or failed branch to carry an id.

So an async agent's id arrives at launch and is held from then on, but a
synchronous subagent cut off by a limit still leaves nothing to send to. Its
transcript is on disk regardless, under

    ~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl

which `claude_history.subagent_dir` already knows how to find. Recovering ids
by listing that directory, and offering them on ISSUE-058's resume card, is the
piece still missing.

## Acceptance / done-when

- A live `tool_result` closing an `Agent` call carries `agent_id`, and
  `agent_description`, `agent_model` and `agent_status` when the record has
  them.
- One reader for both paths, so they cannot drift.
- An ordinary tool's result gains nothing: every tool has a result record, and
  the id is what says which one was an agent.

## Notes & worklog

Done. `_subagent_meta` became `claude_history.subagent_meta` and now takes the
result record rather than the entry around it, since the live path has the same
record under a different name. `sdk_session._handle_block` merges it into the
`tool_result` event.

The client needed no change: it already branches on `ev.agent_id`, and only
offers to load a transcript when nothing streamed -- which stays true, because
a live session streams the subagent's blocks and a replayed one has none of
them.
