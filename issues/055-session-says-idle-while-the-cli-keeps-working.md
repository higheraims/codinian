---
id: ISSUE-055
title: A session says idle while the CLI picks the work back up
status: done
type: bug
area: sdk
created: 2026-08-30
updated: 2026-08-30
related: [ISSUE-054]
---

## Summary

Found while proving [[ISSUE-054]] against a live session, and left out of that
fix deliberately so the two could be tested apart.


A turn that starts a background task ends on a result like any other. The CLI
picks the work back up when the task reports, which can be an hour later, and
everything it produces from then on belongs to no turn Codinian knows about. The
status was set once, when the result arrived, and only a prompt ever set it
back. So the pane said "awaiting input" through the whole second half.

ISSUE-054 made that half visible. This makes the label agree with it.

## Acceptance / done-when

- Content arriving on a session that shows idle puts it back to working.
- A rate limit push or a task notification on a genuinely idle session does not,
  because nothing would bring the status back afterwards.
- A prompt queued while work has resumed is still delivered, not held.

## Notes & worklog

The live evidence, from the session the report came from. `result` at 15:35:58,
`awaiting_input` a moment later, then nothing at all for fifty-one minutes while
a build ran, and forty-five events between 16:27:09.117209 and 16:27:09.121964
once a prompt caused a read. The status stayed `awaiting_input` throughout.

Only an assistant or user message counts as work. A `rate_limit_event` arrives
on genuinely idle sessions and a task notification can too; a status that went
to working on one of those would sit there with nothing to bring it back, which
is the same wrong label pointing the other way.

The send queue is deliberately not held. `_run` waits on `_turn_done` for the
turn it started, and making it also wait on work that resumed by itself would
hang every queued prompt if that work never produced a result. A label that lags
is worth fixing; a composer that swallows messages is not worth risking to fix
it.

## Resolution

`_read` calls `_resume_if_idle` on an `AssistantMessage` or `UserMessage`, before
handing the message on, so a client sees the session go busy and then sees what
it is busy with. `_emit_status` now records what it emitted, which is what makes
"are we showing idle?" a question the reader can answer.

### What was checked

A client that ends a turn on a result and then, a moment later, produces more
work without being asked:

    working -> [starting the build] -> awaiting_input
            -> working -> [the build finished] -> awaiting_input

And the case the guard is for, a rate limit push arriving after the result on an
otherwise idle session:

    working -> [done] -> awaiting_input

with no spurious `working` after it. ISSUE-054's checks were re-run unchanged:
the trailing block still renders in turn 1, two prompts back to back still
serialise, and a dead stream still errors and refuses sends.
