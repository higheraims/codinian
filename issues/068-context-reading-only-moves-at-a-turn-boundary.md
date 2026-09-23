---
id: ISSUE-068
title: The context reading only moves at a turn boundary, so a long turn can compact without warning
status: open
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

- Worth checking whether the CLI announces an auto-compaction it is about to do,
  rather than only the `compact_boundary` after the fact. If it does, that is a
  better trigger than any poll.

## Resolution


