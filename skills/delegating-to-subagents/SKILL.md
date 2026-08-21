---
name: delegating-to-subagents
description: How to split a multi-part task across cheaper subagents and get work back that can be trusted. Use whenever a milestone, feature, or refactor has several separable parts and the user has asked to delegate, spawn agents, use cheaper agents, or parallelise, and whenever you are about to accept a subagent's report as done. Covers choosing the split so two agents never edit one file, writing a contract precise enough to implement against, requiring verification against the running system rather than code reading, and reviewing the returned diff instead of the summary. Includes a reusable Chrome DevTools Protocol driver for verifying browser UI work without a human clicking.
---

# Delegating to subagents

Splitting work out to cheaper agents saves cost and wall-clock on anything with
separable parts. It also introduces one specific failure: a confident report
about work that is subtly wrong. Everything below exists to keep the first
benefit without paying for the second.

## When it is worth it

Delegate when the task has parts that touch **different files** and can be
described precisely. A frontend renderer while you hold the Python backend is a
good split. Two agents in one file is not a split, it is a merge conflict with
extra steps.

Do it yourself when the work is one connected judgment call, when the interface
is still being discovered, or when the part is smaller than the prompt needed to
describe it.

Sonnet has been sufficient for well-specified implementation work. Reserve the
expensive model for the parts where the design is not yet settled.

## Writing the prompt

Five things, and the work comes back usable.

1. **The contract, exactly.** Paste the wire messages, field names, error codes
   and data shapes involved. An agent given "wire it up to the approvals API"
   invents an API. An agent given the literal JSON both directions implements the
   one that exists.
2. **Scope as a fence, not a hint.** Name the files it may touch and say plainly
   which it must not, including that you are editing them concurrently. Say
   whether it may commit. It usually should not.
3. **How to verify, with the mechanics.** Say that reading the code is not
   verification, and give the recipe (see `references/verification-recipes.md`).
   Include the port numbers and scratch directories it should use so its browser
   or server does not collide with yours.
4. **How to test without spending money.** If the system calls a paid model, say
   exactly which operations are free. Cheap fixtures and mocks belong in the
   prompt, not left to invention.
5. **What to report.** Ask for the evidence, and ask explicitly for what it could
   not verify or had to guess. Agents answer that question honestly when asked
   and stay silent about it when not.

## Reviewing what comes back

**Read the diff. The report is a claim about the diff, not the diff.**

Reports have been reliable about what was built and unreliable about what sits
next to it. Real examples from one session:

- An agent correctly implemented browser notifications for pending approvals,
  and did not notice that the desktop pane also reports `document.hidden`, so
  every approval would have notified twice.
- An agent implemented a tool-input shape exactly as specified, having never
  seen that tool in real data. Checking 52 stored transcripts showed the tool
  appears zero times in 1800 calls. The code was fine; the claim that it was
  tested would not have been.

So, on every return:

- Check the factual claims against real data yourself when data exists.
- Look for the case one step beyond the requirement: the second client, the
  hidden pane, the empty list, the resumed session.
- Treat "I could not verify X" as the most valuable sentence in the report and
  decide whether X matters.

When a subagent reports a bug in code you wrote, check it before believing it,
and check it before dismissing it. Both have happened.

## Keeping the work honest afterwards

If a subagent worked around a problem rather than fixing it, fix it properly at
the source before committing. In one case an agent compensated in JavaScript for
a backend that never broadcast session updates; the client workaround was
reasonable, but the backend was where the bug lived.

Record what a subagent could not verify in the commit message or issue notes, so
the next person does not read defensive code as tested code.
