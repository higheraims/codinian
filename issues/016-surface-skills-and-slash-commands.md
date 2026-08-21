---
id: ISSUE-016
title: Surface skills and slash commands in the GUI
status: done
type: feature
area: gui
created: 2026-08-16
updated: 2026-08-16
related: [ISSUE-018]
---

## Summary

Skills and slash commands are discoverable in the CLI by typing `/` and reading the list.
In a GUI that affordance has to be built, or the features are invisible: a user has to
already know a skill exists to invoke it.

List the skills and slash commands available to a session, with their descriptions, and let
one be invoked from the interface. Show in the transcript when a skill loads, so the reason
the model changed approach mid-turn is visible rather than mysterious.

Backlog item from the M4 list.

## Acceptance / done-when

- A running session lists the skills and slash commands it can use, with descriptions.
- Picking one sends the invocation.
- A skill loading appears in the transcript as its own event.

## Notes & worklog

- Relates to [[ISSUE-018]]: a browse capability shipped as a skill is one of the things this
  view should list.

## Resolution

### The SDK does expose this, from one call

Scoping said there was no live way to list skills. There is, and it answers both
halves at once: `ClaudeSDKClient.get_server_info()` returns a `commands` array
of `{name, description, argumentHint}` (nine entries also carry `aliases`), and
**skills appear in it beside slash commands** — `no-ai-slop` sits next to
`/compact` and `/model` and is invoked the same way. On this machine it returns
48 entries. It also carries `agents`, `models` with their supported effort
levels, `account`, `output_style` and the current permission mode, none of which
this issue needed but all of which are there.

That was settled by probing rather than by reading: connecting a session without
ever calling `query()` costs no tokens, so the real shape was cheap to obtain.
The earlier read-only scoping could not determine it, because the exchange is a
control-protocol round trip that is never written to the stored transcript.

`SdkSession` asks once at connect and caches the answer; a failure there is
swallowed, because not knowing the command list is not a reason for a session to
fail to start.

### The three criteria

**Lists them with descriptions.** `GET /api/sessions/{id}/commands`, and a
palette above the composer that opens when the message starts with `/` — the
affordance the CLI gets for free and a GUI has to build. It filters as you type,
on name and on alias, with arrow keys, Tab/Enter to accept and Esc to close. An
empty list and a no-match are distinct states and say so.

**Picking one sends the invocation** — deliberately not quite. Picking
**inserts** `/name ` and leaves the cursor there rather than sending. Most
commands take arguments (`/model <model>`, `/effort <level>`) and sending on
click would fire them empty. The argument hint is shown in the row so the user
knows what to complete.

**A skill load appears as its own event** — partially, and worth stating
plainly. A `Skill` call already arrives as an ordinary `tool_use` event, so it
was always *an* event; what it lacked was any sign that it was different from a
file read. Its card is now marked, so the point at which the model changed
approach mid-turn is visible. There is no separate `skill_loaded` event type,
because that would duplicate the tool call rather than describe anything the
transcript did not already have.

### One bug this turned up in my own code

`refreshPalette` is async and had no guard against an older lookup resolving
last. The first call for a session awaits a fetch while later ones hit the
cache, which is exactly the ordering that goes wrong: typing `/mod` rendered the
unfiltered list because the `/` lookup finished after it. Same request-counter
guard the history search already uses. Caught by driving the page, not by
reading the code.

### What was checked

The route: six commands for a live session, 404 for an unknown one, 401 without
a token. On the real page: `/` opens with all six and the header counting them,
`/mod` narrows to `/model`, `/review` matches `/code-review` **by alias**,
`/zzz` shows a no-match state, and clicking a row inserts `/no-ai-slop ` and
closes the palette.

The command list used in tests is a subclass of the real `SdkRuntime` with only
`commands` overridden, so everything else on the path is the code that ships,
and its shape is copied from a live `get_server_info()` rather than invented.
