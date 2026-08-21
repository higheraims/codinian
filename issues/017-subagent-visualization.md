---
id: ISSUE-017
title: Subagent and multi-agent visualization
status: done
type: feature
area: gui
created: 2026-08-16
updated: 2026-08-16
related: [ISSUE-003, ISSUE-005]
---

## Summary

A session that spawns subagents produces work the main transcript reports only as a `Task`
tool call and a result. What the subagent did in between is where the time and the tokens
went, and it is invisible.

Render subagent activity as its own nested transcript inside the parent's tool card:
which agent, what it was asked, what it did, what it returned. With several subagents
running at once, showing which are still working is the difference between a session that
looks hung and one that is visibly busy.

Backlog item from the M4 list, and the one most dependent on what the SDK actually exposes
about subagent internals.

## Acceptance / done-when

- A `Task` tool call renders as an expandable nested transcript rather than an opaque card.
- Concurrent subagents are individually identifiable, with their status.
- The parent transcript stays readable when a subagent produces a long run of events.

## Notes & worklog

### Scoped 2026-08-20: the SDK exposes everything this needs

The issue called this "the one most dependent on what the SDK actually exposes".
It exposes all of it. Measured against real transcripts in
`~/.claude/projects/` for this project.

**The tool is `Agent`, not `Task`.** Rename in the acceptance criteria above.

**Live.** `ClaudeAgentOptions.forward_subagent_text` (default `False`) is the
switch. Its docstring is the important part: even with it off, a subagent's
`tool_use` and `tool_result` blocks **are already emitted**, as
`AssistantMessage` / `UserMessage` whose `parent_tool_use_id` is the spawning
`Agent` tool_use id. Turning it on adds the subagent's text and thinking.

That means Codinian has a bug today, not just a missing feature:
`_handle_message` never reads `parent_tool_use_id`, so a subagent's tool calls
are already arriving and rendering as ordinary top-level tool cards. Work done
by a subagent is not merely invisible, it is **misattributed to the main
agent**, interleaved with its own calls.

**History.** Subagent transcripts are on disk, one file per subagent, at:

    ~/.claude/projects/<project>/<session-uuid>/subagents/agent-<agentId>.jsonl

They carry the same message shape as the main transcript, so `claude_history`'s
existing `_assistant_events` / `_user_events` parse them unchanged.

The premise in the summary above is wrong for this SDK version: the main
transcript contains **zero** sidechain entries, so there is nothing there being
skipped. The subagent work was never in that file.

**Linking parent to child.** Not `sourceToolAssistantUUID`, which does not
resolve. The parent's `tool_result` entry carries `toolUseResult.agentId`, which
is exactly the filename. Verified 4/4 on session `6d55b8db`. The same
`toolUseResult` also carries `description`, `prompt`, `resolvedModel` and
`status`, which is everything a card header needs: which agent, what it was
asked, what model it ran on, whether it finished.

**Volume, and why the nested transcript must be lazy.** Those 4 subagents
produced **7.2 MiB** of transcript against a 9.4 MiB parent; another session's 5
subagents produced 8.1 MiB. Nesting that inline would dwarf the parent. The
"parent stays readable" criterion above is therefore not a nicety: fetch a
subagent transcript when its card is expanded, not when the card is drawn. See
the wire measurements in [[ISSUE-033]] for why that matters over the tailnet.

- Confirm during the spike ([[ISSUE-002]]) whether subagent events reach the parent stream
  at all. If they do not, this issue reduces to showing the Task call and its result more
  clearly, and should be re-scoped rather than left as written.

## Resolution

Two halves, and the first was a bug rather than a missing feature.

**Live.** The SDK forwards a subagent's `tool_use` and `tool_result` blocks with
`parent_tool_use_id` set to the spawning `Agent` call, whether or not
`forward_subagent_text` is on. `_handle_message` never read that field, so those
blocks arrived indistinguishable from the main agent's and were drawn beside its
own calls. Events now carry `parent_tool_use_id` and the renderer routes by it.

An `Agent` card gets a nested transcript, collapsed, captioned with a running
event count. Bubble grouping travels with the destination, so a subagent's
consecutive text groups inside its own card rather than joining whatever the
parent rendered last. Tool cards stay keyed globally by `tool_use_id`, so a
result pairs with its call wherever either of them rendered.

Approvals deliberately stay at the top level wherever they originate. An
approval folded inside a collapsed subagent is one nobody answers.

**History.** `claude_history` learned the on-disk layout. A `tool_result`
closing an `Agent` call now carries `agent_id` (plus description, model and
status, all read from the parent's own `toolUseResult` rather than by opening
the subagent's file), and `GET /api/sessions/{id}/subagents/{agent_id}` returns
that subagent's events. The card offers a Load button rather than seeding, for
the reason in the scoping notes: those transcripts run larger than the parent.

The line parser was extracted so the main transcript and each subagent's share
it; they differ only in whether sidechain entries are kept, which is exactly
what a reader of each file wants.

The agent id lands in a filename, so it is validated rather than trusted, and an
id that does not resolve returns an empty list rather than an error: a subagent
that was launched and recorded nothing is a real state.

**One thing the screenshot caught.** A subagent's opening turn is the briefing
its parent handed it, and rendered as a bubble it filled the entire card before
any work showed. It is now folded and labelled `Briefing`, the same treatment
[[ISSUE-026]] gave injected turns. The live path already handled this, since
those arrive tagged `injected`.

### What was checked

The parser against the real transcripts on disk: all four `Agent` calls in
session `6d55b8db` link to a readable file, the subagent's tool calls are absent
from the parent's event list, path traversal and malformed ids are rejected, and
truncation still tags its marker.

The route: 25 events for a real agent id, 401 without a token, an empty list for
a traversal attempt and for an unknown id.

The client, on the real page against the real server: with a subagent's blocks
interleaved with a main-thread `Bash` call, three `Read` cards and two text
blocks land inside the `Agent` card while the `Bash` stays at the top level and
the approval stays at the root. On replayed history, four `Agent` cards each
offer a Load button captioned with description and model; clicking one renders
11 nested tool cards and 3 text blocks and updates the caption to 25 events.

### Not done here

Concurrent subagents are identifiable by their own cards but there is no
aggregate "3 running" indicator, and a subagent's own status is whatever its
parent card shows. That is worth revisiting once [[ISSUE-033]] adds streaming,
since the two share the question of what "still working" looks like.
