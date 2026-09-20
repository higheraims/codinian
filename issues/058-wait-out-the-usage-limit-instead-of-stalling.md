---
id: ISSUE-058
title: Wait out a usage limit and carry on, instead of leaving the turn stalled
status: open
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

## Open question, and it changes the work

Auto-resume or offer it? Waiting five hours and then sending a prompt nobody is
watching is a different product from a card that says "blocked until 14:05 --
resume then?" with a button. The second is smaller and cannot surprise anyone;
the first is what the CLI does.

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

Upstream has an open feature request, anthropics/claude-code#94222, for the
related subagent case: a subagent killed by a usage limit is resumable via
`SendMessage` but is not reported as such, and drops off `ListAgents` after a
session restart. No official reply on it yet. If that ships, sessions Codinian
drives may inherit part of it for free -- but only the subagent half, and only
inside a turn that is still alive.
