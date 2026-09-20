---
id: ISSUE-057
title: Question card offers one freeform answer for the whole card, not per question
status: done
type: feature
area: remote
created: 2026-09-20
updated: 2026-09-20
related: [ISSUE-050]
---

## Summary

`buildQuestionCard` in `remote/static/app.js` renders one "or answer in your own
words" input under all questions. The CLI's own AskUserQuestion dialog gives every
question its own Other option. With a four-question card the user could not pick
options for three questions and answer the fourth in their own words; they picked a
listed option they did not mean to avoid retyping the other three answers into the
single freeform box (hit 2026-09-20 in a MyCal session).

## Acceptance / done-when

- Each question block gets its own Other input; a typed value counts as that
  question's answer and lands in `answers[qtext]`, not in the card-global `response`.
- The card-global freeform can stay for whole-card replies, but a per-question Other
  must not require abandoning the other questions' picked options.
- `composedAnswer` keeps pairing each answer with its question text.

## Notes & worklog

Filed from a MyCal session after the user hit the limitation live.

Done. Every question block now carries its own Other input, styled from
`.question-freeform` so the two read as one control. Single-select treats the
typed answer as one of its choices: typing clears the picked option and picking
clears the typed text, since a radio group cannot hold both. Multi-select takes
options and a typed answer together. `answerFor` composes one question's answer
from both halves, and `answers`, `anyChosen` and `composedAnswer` all go through
it, so the pairing with question text is unchanged.

Verified against the running page rather than the protocol: the mock harness
already exposes its director (`?mock=1&session=a1c93f02`), so a driver page
pushed a four-question `question_request` into a live pane, picked options for
three questions, typed into the fourth's Other, and read the outgoing `answer`
off the socket. All four answers arrived under their own question text, with
`response` still null. The multi-select case returned "Fedora, Debian, Ubuntu
too"; both directions of the single-select radio rule hold.

The mock has no question-card session of its own, which is why nothing caught
this earlier. Worth adding.
