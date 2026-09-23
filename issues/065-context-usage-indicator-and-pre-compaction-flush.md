---
id: ISSUE-065
title: No warning before a session compacts, and no chance to save anything first
status: done
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

- 2026-09-23: Scoped alongside [[ISSUE-064]], which is the after-the-fact
  marker. This is the advance warning. Doing 064 alone leaves the habit broken,
  since a line that appears after compaction is too late to act on.

- **The reading is free, unlike plan usage.** `_capture_plan_usage`
  (`sdk_session.py`) refuses to ask the CLI anything because asking costs a turn
  against the limit being reported, and waits for a `/usage` the user ran. That
  reasoning does not carry over. `get_context_usage` is a control request handled
  by `_query`, the same family as the `get_server_info()` call already made in
  `start`, so it is polled at the end of every turn.

- **Measured against a real CLI rather than read off the type hints.** A throwaway
  `ClaudeSDKClient` on Opus 5 answered `totalTokens 18627`, `maxTokens 1000000`,
  `rawMaxTokens 1000000`, `percentage 2`, `isAutoCompactEnabled True`,
  `autoCompactThreshold 967000`. Two things follow. The field names are camelCase,
  which is what the backend now reads. And `maxTokens` is **not** reduced by an
  autocompact buffer, so the threshold sits at 96.7% of the window rather than at
  100%. A fixed amber point would be useless on a million-token window and wrong
  on a smaller one, so the warning is placed 15 points below whatever line the CLI
  names, falling back to 90% when it names none.

- **One implementation covers both clients**, because the desktop transcript pane
  renders the same `app.js` the browser does.

- **The injection ships off by default**, which is what the original scoping
  argued for. It spends a turn and lands mid-work, and that is a judgement about
  someone's work rather than about a display. `context_flush_inject` and
  `context_flush_percent` are in `agent_options.DEFAULTS`; the percentage is
  clamped to 50-99 because 0 fires on every session's first turn and 100 fires
  after the CLI has already compacted.

- **It sends rather than queues.** `manager.queue_inject` is what the browser's
  inject endpoint uses, and that queue is drained by `window.py` alone, so a
  session running with no GTK window would have queued the prompt and never
  delivered it. `SdkSession.send` works either way. It gained a `source`
  parameter so the prompt is echoed as `injected` rather than `operator`: the
  user did not type it, and a transcript saying they did is wrong. A `note`
  alongside says the interruption happened and why.

- **Asked once per cycle, not once per turn over the line.** `_flush_asked` is
  set when the prompt goes out and cleared when a `compact_boundary` arrives.

- Verified in the mock, which gained the sequence that matters in session
  `b2f9013c`: a reading at 88% draws amber, the boundary clears it rather than
  leaving a full window reported against a conversation that is now a summary,
  and the next turn puts 12% back.

- **Not verified:** the injection firing, which needs a session that actually
  crosses the threshold. The path it takes is `send`, which every typed message
  already uses.

- **2026-09-23, verified on the live wire**, which the note above could only say
  it had not been. A running session emitted

  ```
  {"type": "context_usage", "seq": 8, "total_tokens": 19549,
   "max_tokens": 1000000, "percentage": 2, "auto_compact": true,
   "threshold": 967000}
  ```

  so the camelCase read and the snake_case emit both hold outside the mock.

  Asking that question also found what the mock could not express: the reading
  only moves at a turn boundary, so a turn long enough to compact in the middle
  of itself never gets the warning or the flush. Filed as [[ISSUE-068]].

## Resolution

Polled after each turn, emitted as `context_usage`, drawn in the session footer
beside the plan windows. The threshold injection exists and is off until asked
for.
