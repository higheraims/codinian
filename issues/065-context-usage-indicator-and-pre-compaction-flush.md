---
id: ISSUE-065
title: No warning before a session compacts, and no chance to save anything first
status: open
type: feature
area: sdk
created: 2026-09-23
updated: 2026-09-23
related: [ISSUE-011, ISSUE-056, ISSUE-064]
---

## Summary

The CLI shows context left until auto-compact. Codinian shows nothing, so the
first sign a session is about to lose its context is that it already has. The
working habit this breaks: seeing the indicator move and asking the agent to
write anything worth keeping to memory before the summary lands.

The figure is not on the wire. Every system subtype that has ever appeared in
this machine's transcripts is `api_error`, `turn_duration`, `local_command`,
`away_summary`, `bridge_status`, `compact_boundary`, `stop_hook_summary`,
`model_refusal_fallback` and `informational`. None carries a context reading;
the CLI computes its own for display.

It can be asked for. `ClaudeSDKClient.get_context_usage()` (`client.py:471`)
returns what `/context` shows: `totalTokens`, `maxTokens`, `percentage`,
`isAutoCompactEnabled` and `autoCompactThreshold`.

## Acceptance / done-when

- `sdk_session` calls `get_context_usage()` at the end of each turn and emits a
  `context_usage` event, so every client gets it live and on replay.
- The session footer shows percent of context used, next to the plan usage and
  limit indicators already in `#session-footer`. Amber as it approaches
  `autoCompactThreshold`, and nothing at all when `isAutoCompactEnabled` is
  false.
- Crossing a threshold injects a prompt asking the agent to write anything worth
  keeping to memory before compaction, through the same path as
  `/api/sessions/{id}/inject`.
- The threshold and whether the injection happens at all are settings, not
  constants.
- A `compact_boundary` clears the reading rather than leaving the last
  pre-compaction figure on screen.

## Notes & worklog

- 2026-09-23: Scoped alongside [[ISSUE-064]], which is the after-the-fact marker.
  This is the advance warning. Doing 064 alone leaves the habit broken, since a
  line that appears after compaction is too late to act on.

- **The reading is free, unlike plan usage.** `_capture_plan_usage`
  (`sdk_session.py:1060-1074`) refuses to ask the CLI anything because asking
  costs a turn against the limit being reported, and waits for a `/usage` the
  user ran. That reasoning does not carry over. `get_context_usage` is a control
  request handled by `_query`, the same family as the `get_server_info()` call
  already made at `sdk_session.py:325`, so it costs no turn and no tokens. Poll
  it.

- **One implementation covers both clients**, because the desktop transcript pane
  renders the same `app.js` the browser does.

- **The injection is the part worth arguing about.** A human-facing indicator
  only works if a human is watching, which is the failure this issue exists to
  fix. But an injected prompt spends a turn and interrupts whatever the agent was
  doing, so the interaction with [[ISSUE-056]] needs checking before it is turned
  on by default. A first threshold of 80% is a guess and should be tuned against
  a real session.

- **`PreCompact` hooks are not the mechanism.** The SDK supports the event
  (`types.py:270`, input at `types.py:366` carrying `trigger` and
  `custom_instructions`), and `ClaudeAgentOptions` at `sdk_session.py:283-291`
  already registers `PreToolUse` and `PostToolUse`, so adding it is small. But it
  fires once compaction is already starting, with the model not taking a turn, so
  it cannot make the agent write anything. It is good for snapshotting the
  transcript aside and for emitting the boundary early; the flush has to happen
  before it.

## Resolution
