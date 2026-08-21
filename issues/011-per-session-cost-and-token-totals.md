---
id: ISSUE-011
title: Per-session cost and token totals
status: done
type: feature
area: gui
created: 2026-08-16
updated: 2026-08-19
related: [ISSUE-003, ISSUE-005]
---

## Summary

Every SDK result message carries usage, so the numbers are already arriving as `usage` and
`result` events. Accumulate them per session and show a running total: input, output, and
cache tokens, plus cost where the SDK reports it. Across several long-running sessions
this is the difference between knowing which one is expensive and guessing.

The transcript footer from [[ISSUE-005]] is the natural place for the current session's
total; the sidebar can carry a compact figure per session.

## Acceptance / done-when

- A live session shows a running token and cost total that updates as results arrive.
- The total survives across turns within a session.
- A resumed session states plainly whether the total covers the whole conversation or only
  this run, rather than showing an ambiguous number.

## Notes & worklog

- Whether totals persist across app restarts depends on the persistence choice made in
  [[ISSUE-003]].
- 2026-08-19: Settled the semantics by measuring rather than guessing. Across two turns in
  one session, `total_cost_usd` rose from 0.120077 to 0.126495 while the per-turn token
  counts stayed the same, so **cost is cumulative for the conversation and token counts are
  per turn**. Cost is therefore assigned and tokens are summed.
- 2026-08-19: `Session` accumulates input, output, cache_read and cache_creation tokens, and
  `meta()` carries them. A resumed session sets `totals_cover_this_run_only`, because seeded
  history carries no usage events and its cost is not in the SDK's number either; showing a
  total that looks like the whole conversation would be a lie.
  Verified live: totals accumulate across a turn and appear in session meta. Remaining: the
  footer and sidebar display.- 2026-08-19: **`ResultMessage.usage` is the wrong source and the first implementation using
  it was wrong.** It describes only the last API call of a turn and comes back all zeros
  often enough to be useless: across three turns in one session it reported 0 input and 0
  output for the middle turn while real work happened. Summing it understates the total, and
  the transcript printed "0 in / 0 out", which reads as a bug.
  `ResultMessage.model_usage` is the right source: a per-model breakdown that accumulates
  over the conversation, covering the models the SDK uses on the side (a haiku call for the
  session title) as well as the one doing the work. Measured across those same three turns it
  rose 899 to 901 to 903 input tokens and 11905 to 23848 to 35791 cache reads.
  So the backend sums `model_usage` into `tokens_total` on the usage event and the session
  assigns it, keeping per-turn accumulation only as a fallback for a result that carries no
  breakdown. The per-turn line in the transcript now shows token counts only when they say
  something, and the cost alone otherwise.
- 2026-08-19: Fixed a related gap: `add_event` folded status and cost into the session but
  never notified listeners, so the session list a client holds kept whatever status and cost
  it had when the list was last sent. Any session other than the open one showed a stale dot
  and a stale total. It now notifies on `status` and `usage` events, and only those, so
  ordinary text and tool events do not rebroadcast the whole list.
- 2026-08-19: UI done. A footer under the transcript shows cost and the four token counters
  for the open session; the sidebar carries a compact cost and total per session. A resumed
  session says "this run only" next to its figures.

## Resolution

Done. Verified live across two turns of one session: totals grew 899 to 901 input, 11 to 14
output, 11905 to 23848 cache read, cost 0.006975 to 0.013031, and the browser footer and
sidebar matched the backend exactly.
