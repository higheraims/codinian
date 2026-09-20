---
id: ISSUE-058
title: Wait out a usage limit and carry on, instead of leaving the turn stalled
status: in-progress
type: feature
area: sdk
created: 2026-09-20
updated: 2026-09-20
related: [ISSUE-011]
---

## Summary

Claude Code 2.1.271: "Workflows now pause at your usage limit instead of
dropping agents: A dynamic workflow that hits your usage limit now pauses and
resumes when the limit resets instead of dropping agents." The session-level
form of this is the `autoContinueAtUsageLimit` setting, whose own description
reads: "When a claude.ai usage limit stops your session, wait for the limit to
reset and continue the task automatically."

Codinian gets none of it, and cannot get it by turning anything on.

## Why it does not arrive on its own

The CLI gates the whole auto-continue machinery on the session being
interactive. In the 2.1.267 bundle the entry point is

    F()  = hu() && !_t()                      // hu() = launchOptions.isInteractive()
    de() = F() && <feature enabled>
    _de(e) = <signed in> && billingType !== "usage_based" && rejected(e) && de()

where `rejected(e)` is `status === "rejected"` with a finite `resetsAt` and no
overage in play. A session the Agent SDK drives is never interactive, so `F()`
is false before any of the rest is consulted. There is no `ClaudeAgentOptions`
field that flips it, and `--settings` cannot override an interactivity check.

So this is ours to build, or not to have.

## What we already have

`sdk_session.py` parses the CLI's `rate_limit_event` and emits every field of
it, `resets_at` included (`_handle_message`). `app.js` renders it as a banner
that already says "Session limit reached ... resumes <time>". The facts needed
to act are on screen. Nothing acts on them.

What happens today: the limit lands, the banner appears, the turn ends, the
session sits in `awaiting_input`, and the work stops there until a person
notices the window reopened and re-sends the prompt themselves.

## Decided: offer it

Not auto-resume. A wait that ends by spending a fresh window on a turn nobody is
watching is worse than one that ends with a button, and the card is most of the
value either way: the prompt is held, so nothing has to be retyped.

## Acceptance / done-when

- A turn stopped by a `rejected` rate limit is distinguishable from a turn that
  merely ended. Today both land on `awaiting_input` and look identical.
- The prompt in flight when the limit landed survives the wait, rather than
  being something the user has to remember and retype.
- Whatever resumes says so in the transcript, so a session that carried on
  overnight is legible in the morning.
- Nothing fires after a `close`, and nothing fires twice for one limit.

## Notes & worklog

Checked against claude-agent-sdk 0.2.140 and the CLI at 2.1.267 (latest
published at the time of writing: 2.1.278).

Done, for the main turn. `sdk_session.py` holds a `rejected` limit that carries
a reset time and has no overage behind it, drops it again the moment an
assistant message proves the turn ran, and emits `rate_limit_block` at the end
of a turn that still has one -- carrying the prompt handed to the CLI most
recently. `app.js` renders that as an amber card with the window, the reset
time, the held prompt quoted, and a Resume button that counts down and sends
nothing until the window reopens. Tests in `tests/test_sdk_session.py`; the card
checked in headless Chromium against a live pane, both before and after reset,
with and without a held prompt.

Still open: the sidebar. A blocked session sits in `awaiting_input` and looks
exactly like an idle one from outside the pane, so with several panes open there
is nothing saying which is stalled.

Also still open, and the reason subagents are not covered: see ISSUE-059.

Upstream has an open feature request, anthropics/claude-code#94222, for the
related subagent case: a subagent killed by a usage limit is resumable via
`SendMessage` but is not reported as such, and drops off `ListAgents` after a
session restart. No official reply on it yet. If that ships, sessions Codinian
drives may inherit part of it for free -- but only the subagent half, and only
inside a turn that is still alive.
