---
id: ISSUE-069
title: The context reading only moves at a turn boundary, so a long turn can compact without warning
status: done
type: bug
area: sdk
created: 2026-09-23
updated: 2026-09-23
related: [ISSUE-064, ISSUE-065]
---

## Summary

`_read_context_usage` is called from one place, `sdk_session.py:450`, on
`ResultMessage`. So the figure in the session footer is the figure as of the
last completed turn, and it does not move until the next one completes.

Auto-compaction does not wait for a turn boundary. A turn that crosses
`autoCompactThreshold` in the middle of itself gets compacted by the CLI there
and then, with the footer still reporting whatever the previous turn ended at
and the pre-compaction flush prompt never fired, because [[ISSUE-065]] only
gets the chance to ask at the same boundary.

## Steps to reproduce

Observed rather than constructed, in the session writing this. Asked of the
running instance on 2026-09-23, three and a half hours in:

```
922 events: 769 system, 57 tool_use, 27 thinking, 15 text
  1 usage          seq 6   06:32:06
  1 context_usage  seq 8   06:32:06  percentage 2, total 19549
```

One turn had completed, the `/model` command in the first minute. Everything
after it is a single turn still running, and the only reading the footer has is
the one from before that turn started.

## Acceptance / done-when

- The reading moves during a turn, not only at the end of one.
- A turn long enough to cross the threshold gets the flush prompt while there is
  still something to flush.
- Whatever the polling interval turns out to be, it does not cost a turn or a
  token, which is the property that justified asking at all.

## Notes & worklog

- 2026-09-23: Found by asking the live path what it had actually emitted, after
  [[ISSUE-065]] recorded that its reading was "verified in the mock". The mock
  replays a scripted sequence of turns, so every reading in it lands at a turn
  boundary by construction and a turn that never ends cannot be expressed.

- The event itself is correct on the live wire, which is worth writing down
  because it had not been seen there before:

  ```
  {"type": "context_usage", "seq": 8, "total_tokens": 19549,
   "max_tokens": 1000000, "percentage": 2, "auto_compact": true,
   "threshold": 967000}
  ```

  camelCase in, snake_case out, and the values agree with what a throwaway
  client measured off the CLI in [[ISSUE-065]].

- `get_context_usage()` is a control request on `_query`, so polling it costs
  nothing against the limit. The open question is what to hang the poll on. Per
  message is too often on a turn that emits hundreds; a timer is simple but runs
  on an idle session as well; every N tool results is cheap and tracks the thing
  that actually grows the context.

- **The open question above, answered: it announces, and the announcement is
  still no use as this trigger.** `PreCompact` is a hook event the SDK declares
  alongside the `PreToolUse` and `PostToolUse` this app already registers, and
  it fires. A throwaway client on Haiku 4.5 with one registered, sent
  `/compact`:

  ```
  PreCompact at +0.01s
    trigger: manual
    keys: custom_instructions, cwd, hook_event_name, prompt_id,
          session_id, transcript_path, trigger
  ```

  So there is notice, and it says whether a person asked or the CLI decided.
  What there is not is time. Compaction begins as the hook returns, and what
  this ticket wanted the warning for is a turn: asking the session to write
  down what matters means the model generating, and it cannot generate while
  its conversation is being replaced. A hook that blocked to buy time would
  block the session it was trying to give work to.

  The poll therefore stays what decides when to ask, and the percentage
  threshold stays what it is measured against.

  Two things `PreCompact` would be good for, neither of them this:
  clearing `_flush_asked` and `_last_context_shown` when compaction starts
  rather than when it ends, and saying so on screen while it runs. Filed as
  [[ISSUE-071]].

  Not verified: that it fires for `trigger: auto` as well as `manual`. Reaching
  an automatic compaction means a million tokens, which is the same reason
  [[ISSUE-064]] could not test its own renderer.

- Nothing announces it in the stored transcript, which is the other half of the
  same question. Both `compact_boundary` records on this machine are preceded
  only by ordinary traffic: session bookkeeping in one
  (`attachment`, `last-prompt`, `ai-title`, `mode`, `permission-mode`,
  `bridge-session`) and six `user` records in the other.

- **The question that had to be answered first: does the CLI recompute this
  mid-turn at all?** If `get_context_usage` only measured at a turn boundary,
  polling would have returned a stale number more often rather than a live one,
  and the whole ticket would have been about the CLI rather than about us.

  It recomputes. A throwaway client on Haiku 4.5, asking on every message of a
  two-turn conversation:

  ```
  turn1 msg  1  18440  9%     turn2 msg  1  33080  17%
  turn1 msg  2  21863  11%    turn2 msg  3  33230  17%
  turn1 msg 34  33080  17%    turn2 msg 14  35414  18%
  turn1 msg 89  33080  17%    turn2 msg 37  35414  18%
  ```

  It tracks the last request the model made, so it steps when a tool result
  goes back and holds between. Turn one grew 14,640 tokens from its second
  message to its end, none of which the footer could show. The request also
  answers while the turn is generating: 88 of them in a row cost nothing
  visible.

- **What the poll hangs on is messages, not a timer.** `CONTEXT_READ_INTERVAL`
  is fifteen seconds of elapsed time checked when a message arrives, so an idle
  session asks nothing and there is no task to cancel on close. The result that
  ends a turn passes `force=True` and skips the gate, since that figure is what
  the session rests at until someone types again.

- **Readings are deduplicated by what the footer draws**, which the ticket did
  not anticipate and which matters more than the interval. Every event is kept
  for the life of the session and re-sent to every client that subscribes, so
  fifteen-second polling on a three-hour turn would have added 720 events. The
  key is the rounded percentage plus the values that change the strip's shape,
  so a long turn leaves one event per point it climbed. The exact token count
  in the tooltip can trail by up to a percent of the window as a result.

  The flush check runs on every reading, including a suppressed one. Whether to
  interrupt a turn that is about to lose its context does not depend on the
  footer having changed.

- **No timeout is wrapped around the request.** `_send_control_request` bounds
  itself at 60 seconds and drops its entry from `pending_control_responses`
  when its own `fail_after` fires. Cancelling it from outside raises
  `CancelledError` instead, which skips that cleanup and leaks the entry, so an
  `asyncio.wait_for` here would trade a rare stall for a slow leak.

- **Verified on the live path and then at the page.** A real `SdkSession`
  driven against the CLI emitted a reading at seq 4 carrying 17,300 tokens and
  another at seq 19, the turn's result, carrying 34,660. Only the second would
  have existed before. The mock gained the climb inside one turn, 74% to a tool
  call to 88%, and the page rendered in headless Chromium walks it:

  ```
  1.4s  context-usage           Context 74%   148,000 of 200,000 tokens
  3.0s  context-usage is-near   Context 88%   176,000 of 200,000 tokens
  3.7s  hidden                                (compact_boundary)
  6.3s  context-usage           Context 12%    24,500 of 200,000 tokens
  ```

- ISSUE-065 shipped with a mock case and no Python test, which is why this went
  unnoticed. The eight tests added here cover the gate, the force at a turn
  boundary, the dedupe, the boundary reset, and the flush firing mid-turn.

- **Filed as 068 and renumbered to 069 the same day.** 068 was already taken by
  "Improve project view in desktop", written in Codinian while this was being
  worked on. The number was hardcoded here rather than asked for: `next_id` was
  called with the wrong signature, raised `TypeError`, and the answer was
  guessed from a directory listing taken hours earlier instead of the call
  being fixed. `next_id('.', 'issues')` now returns 069, which is where this
  landed.

  Nothing was lost; the two files never shared a name. The commits below the
  renumber still say 068 in their subjects, because rewriting them would be a
  worse trade than a line here saying so: `855013a` files this ticket and
  `aa2f74c` implements it.

## Resolution

The reading is taken as messages arrive, gated to one every fifteen seconds,
and forced on the result that ends a turn. Readings that would draw the same
footer are dropped rather than stored, so a long turn leaves one event per
percentage point instead of one per interval. A compaction boundary clears both
the interval and the last-shown reading, since clients clear the footer there.
