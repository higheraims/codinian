---
id: ISSUE-076
title: One permission note per tool call fills the transcript with lines about work it does not show
status: done
type: bug
area: remote
created: 2026-09-24
updated: 2026-09-24
related: [ISSUE-027, ISSUE-017, ISSUE-072]
---

## Summary

In Auto (and any other mode that answers for the user) every tool call emits a
`permission_note`, and the renderer appends each one to the transcript root as
its own line. A turn running three subagents produced a screen with nothing on
it but "Bash left to the CLI by Auto", repeated down the whole pane.

The reason none of the calls are visible beside those lines is ISSUE-017's
routing. A subagent's `tool_use` and `tool_result` blocks carry
`parent_tool_use_id` and render inside the collapsed Agent card. The
`permission_note` carries only `tool_use_id`, so `targetFor` has nothing to
route it by and it lands at the root. The parent transcript becomes a list of
notices about work that is drawn somewhere else.

ISSUE-027 added the note so that a session which stops prompting says what
stopped it. One indicator does that; one per call does not.

## The ordering that constrains the fix

A probe against a real session, logging PreToolUse hook fires against the
`tool_use` blocks arriving on the stream:

```
(1, BLOCK, Bash,  toolu_015Sxi…, parent=None)
(2, HOOK,  Bash,  toolu_015Sxi…, agent_id=None)
(3, BLOCK, Agent, toolu_018VpE…, parent=None)
(4, HOOK,  Agent, toolu_018VpE…, agent_id=None)
(5, HOOK,  Bash,  toolu_01ExHC…, agent_id=ac66e03c8874f0486, agent_type=Explore)
(6, BLOCK, Bash,  toolu_01ExHC…, parent=toolu_018VpE…)
```

On the main thread the block precedes the hook, so the card exists when the
note arrives. Inside a subagent the order reverses at lines 5 and 6. A fix that
looks the card up on arrival works for main-thread calls and misses exactly the
subagent calls that cause the flood, so the note has to be parked by
`tool_use_id` and applied when its card renders.

The same probe shows the hook input carries `agent_id` and `agent_type` for
subagent calls and neither on the main thread. `_pre_tool_use` reads neither.

## Acceptance / done-when

- A tool call that skipped the human says so on its own card, not on a line of
  its own.
- A subagent's notes do not reach the parent transcript.
- A note whose card never renders is still reported, counted rather than
  repeated.

## Notes & worklog

- Measured alongside ISSUE-072: in Auto this adds one root-level node per tool
  call, against the 1,210 tool cards in the pane measured there.

## Resolution

The note no longer renders as a line. `buildToolCard` gained
`setPermissionNote`, which appends a faint badge to the card head saying which
mode let the call through, with the full ISSUE-027 sentence as its tooltip. A
note whose card has not rendered is parked in `renderState.pendingNotes` and
claimed by the `tool_use` case when that card appears, which is what makes the
subagent ordering above work. Anything still parked when a `usage` event ends
the turn, or when a replay runs out of events, is drawn as one counted line:
"2 tool calls left to the CLI by Auto".

Routing by `tool_use_id` turned out to be enough, so `agent_id` stays unread.
Carrying it would only have said which subagent a badge already sitting inside
that subagent's Agent card belonged to.

Checked by driving the page in headless Chromium and replaying the ordering
above, 20 subagent calls with the note ahead of the block:

```
rootSystemLines  []
badges           22 (21 of them inside the Agent card)
firstBadge       "Auto", title "Bash left to the CLI by Auto"
after usage      ["WebFetch left to the CLI by Auto"]
```

Clicking away and back re-renders the whole stored transcript, and the replayed
DOM matches the live one: 6 badges, 1 at the root, no system lines.

The mock's own note pointed at a `tool_use_id` no card carried, so it could
only ever have rendered detached. It now rides on the Edit that `acceptEdits`
approves.
