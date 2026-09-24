---
id: ISSUE-073
title: The context reading deadlocks against the message loop it runs inside
status: done
type: bug
area: sdk
created: 2026-09-24
updated: 2026-09-24
related: [ISSUE-065, ISSUE-069]
---

## Summary

`_read_context_usage` was awaited inside `async for msg in
receive_messages()`. The answer to that request arrives on the same stream the
loop reads, so a loop waiting for it is not reading the messages it is queued
behind, and the request can only time out. The SDK gives up after 60 seconds,
the loop takes one message, and because `_context_read_at` is stamped before
the request rather than after, the next message is already 60 seconds past the
15-second gate and asks again.

One message a minute, for as long as the backlog lasts.

## Steps to reproduce

A throwaway client, same await in the same place:

```
no backlog, 26 messages      0.78s to 1.47s per request
25-second backlog            60.03s, then raises
```

Observed in a real session first. The CLI wrote a finished 3,299 character
end-of-turn message to its transcript at 07:52:17. Codinian's last stored event
was 07:51:57, and it was still feeding that same text to the page at 08:32,
in frames landing at 08:28:10, 08:29:10, 08:30:10, 08:31:10 and 08:32:10.
Exactly sixty seconds apart, which is what gave it away: generation does not
tick on the minute.

Both live sessions were doing it at once, which is what a shared timeout looks
like and what led the first pass at this to blame the API.

## Acceptance / done-when

- A turn with a backlog behind it delivers at the rate the CLI produces it.
- Readings still arrive during a turn, which is what ISSUE-069 was for.
- The reading at a turn boundary is not lost.

## Notes & worklog

- 2026-09-24: Introduced by `aa2f74c` (ISSUE-068). Before it, the reading only
  ran on `ResultMessage`, where the stream is already drained, which is why
  the docstring could say it "has ended every turn since ISSUE-065 without
  stalling one" and why nothing caught it. That docstring named this exact
  failure and accepted it as a cost; the cost was an hour.

- Subagent traffic is what builds the backlog, so any turn running agents is
  exposed. It is not a rare case.

## Resolution

`_schedule_context_read` starts the reading as a task beside the loop and
returns. One is in flight at a time, which is the property awaiting it used to
provide. A turn boundary arriving while one is running sets `_context_force`
and the runner takes a second pass, so the figure the session rests at is not
dropped.

Five tests, one of them driving `_read` itself: ten messages, no reading
answered, and the loop still has to reach the tenth. Reverting the two call
sites fails it with "loop stalled after 1 of 10 messages".

Checked against a real CLI with the same 25-second backlog that produced the
60.03s measurement above:

```
489 messages drained in 2.7s; readings ok=2 failed=0
```

At the old rate that backlog would have taken over eight hours.
