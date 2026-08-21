---
id: ISSUE-032
title: Sessions run with no system prompt
status: done
type: bug
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-026, ISSUE-030, ISSUE-033]
---

## Summary

Sessions run in Codinian are far less talkative than the same work run in a
terminal. The transcript reads as a list of tool calls with almost nothing
between them.

The cause is that Codinian sets no system prompt, which the SDK turns into an
empty one rather than leaving it alone. Fixing it means deciding what else about
a session should be configurable, so this issue also adds a Claude tab to
Settings for the options that govern how Codinian talks to Claude Code.

## Acceptance / done-when

- A session runs with Claude Code's own system prompt unless the user turns it off.
- The options that shape a session live in one place, including the permission
  mode that ISSUE-030 put in General.
- A setting takes effect on the next session without restarting the app.

## Notes & worklog

- Noticed by comparing a terminal session viewed on the Android app against a
  Codinian session doing the same work.

## Resolution

`sdk_session.start()` built `ClaudeAgentOptions` with four fields and left
`system_prompt` unset. That is not the neutral choice it looks like:

```python
# claude_agent_sdk/_internal/transport/subprocess_cli.py
if self._options.system_prompt is None:
    cmd.extend(["--system-prompt", ""])
```

`None` means "run with no system prompt", not "use the default". Every Codinian
session since M1 has been running without Claude Code's prompt, which is what
produces the narration before a tool call and the summary after. The measured
shape of it: the ISSUE-024 session ran **93 tool calls against 8 assistant text
blocks**. That was read at the time as the model simply not narrating much. It
was never asked to.

`agent_options.py` now maps the stored settings onto `ClaudeAgentOptions`
keyword arguments, and `start()` reads it per session, so a change applies to
the next session rather than needing a restart. The module holds no `gi` import
and no SDK import, which is what let the mapping be tested on its own.

The mapping keeps "default" as a distinct value from any particular setting: a
control left at its default contributes **no key at all**, so the CLI's own
default stands rather than a value of ours that happens to match it today.

### What the tab holds

**System prompt.** A switch, on by default, plus an optional append. Off is
still reachable and still means no system prompt whatsoever; the subtitle says
so rather than letting it read as "a shorter one".

**Model.** Free text, blank for the CLI's choice. Deliberately not a dropdown:
model names change faster than this app ships and a hardcoded list would be
wrong within months. A name that does not exist fails at session start with the
API's own message, which is a better error than a stale menu entry.

**Effort and thinking.** Both were previously unset and invisible. Thinking is
the more interesting of the two, because it fixes something nobody had reported:
every thinking block in the ISSUE-024 transcript is empty. All 46 of them. The
Thinking card in the transcript has always expanded to nothing, because current
models default `display` to `omitted`. Thinking is billed the same either way,
so `summarized` costs nothing and fills the card.

**Permission mode**, moved here from General. It shapes a session like
everything else on this tab, and leaving it behind split one idea across two
tabs. General now holds only the app's own chrome.

### The one combination that fails

Current models reject disabled thinking above `high` effort: Claude Opus 5
returns a 400 rather than clamping. The tab shows a warning row when the two
controls disagree, because the API error names the parameter but not which of
the two to change.

### What was checked

The mapping, without GTK or the SDK: every control at default and at each value,
a config written before this issue, bogus values falling back, and the `append`
riding on the preset. Two checks matter more than the rest — every key the
mapping produces is a real `ClaudeAgentOptions` field, and the resulting object
constructs.

Then the actual CLI argv, built through the SDK's own transport, since the whole
bug lives in one line of it. Before: `--system-prompt ""`. After: no such flag,
and `--model claude-opus-5 --thinking adaptive --thinking-display summarized
--effort xhigh` when those are set.

On a built window: the four tabs and their order, the controls' starting values,
that changing one writes to `config.json` rather than only to the widget, the
conflict warning appearing and clearing, and that permission mode is on the
Claude tab and gone from General.

### Not measured

Turning the preset on adds a sizeable system prompt to every turn. It is cached
after the first request, but the input-token line will move and there is no
measurement here of by how much. Worth watching the totals footer over the first
few sessions.
