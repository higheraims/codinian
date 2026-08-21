---
id: ISSUE-026
title: Show appropriate level of session detail
status: done
type: bug
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-025, ISSUE-027, ISSUE-028]
---

## Summary

Session window does not show much detail from the chat

It just shows "Thinking", any approvals, and the occasional mention of "now this action..." but not much description of what's going on

## Acceptance / done-when

- A reader can follow what a turn is doing from the transcript alone, without opening cards.
- Bulk text the model never wrote (skill bodies, hook output) does not sit in the conversation at full length.

## Notes & worklog

- See screenshots in Docs folder
- in some cases there's too much detail for things we don't need to see - for example, the session read the no-ai-slop skill and echoed the whole skill document into the session chat

## Resolution

Most of the missing detail was not missing. It was rendered and then crushed to
two pixels by the flex bug in [[ISSUE-025]], so the tool cards in the
screenshots are showing their heads and clipping their bodies. Fixing that
brought the input and result sections back on their own.

What was left is a question of what belongs at the top of a card. The session
behind the screenshots ran 93 tool calls against 8 paragraphs of assistant
prose, so tool cards carry the narrative, and a card headed by the bare word
`Bash` does not say what happened. Each card head now carries one line naming
the subject of the call: the command for `Bash`, the path for `Read`, `Edit`
and `Write`, the pattern for `Grep` and `Glob`, the skill name for `Skill`, the
description for `Task`. It is one line, ellipsised, with the full text still in
the body below. The argument JSON that used to be the whole card is now folded
once it passes 8 lines.

Results changed in the other direction. Everything was behind an unlabelled
"Show result", including one-line answers, which is a second reason the
transcript read as a list of things happening to no visible effect. A result of
6 lines or fewer now renders inline; a longer one keeps its toggle, but the
toggle says how many lines are behind it and the first line shows above it.

The skill document had a cause of its own. It arrives as a **user text block**,
not as a tool result: the issue-24 transcript has exactly two user text blocks,
"address issue 24" at 16 characters and the no-ai-slop body at 5,621, and the
CLI marks the second `isMeta`. The renderer had no way to tell them apart, so
it drew a conversation bubble around a skill document. `text` events now carry
an optional `source`, `operator` for what the user typed and `injected` for a
turn the CLI produced itself, and injected ones render as a folded card
labelled with their first line. Replayed history already dropped `isMeta`
entries before they reached the renderer, so it shows nothing there rather than
a folded card; that difference is deliberate and noted in
`docs/transcript-protocol.md`.

One thing was genuinely absent rather than mis-rendered: the user's own prompt.
The SDK does not stream our prompts back, so nothing ever emitted them, and a
session started with a prompt opened on the reply with no sign of the question
(visible in the first screenshot, which begins at "Session initialized" and goes
straight to Claude's answer). `SdkSession.send` now emits the text as it queues
it, which also gives the queued-message feedback [[ISSUE-028]] needed.
