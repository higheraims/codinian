---
id: ISSUE-053
title: The tail of a turn hides under the composer while a prompt is typed
status: done
type: bug
area: remote
created: 2026-08-29
updated: 2026-08-29
related: [ISSUE-025, ISSUE-033]
---

## Summary

Reported from a session left running for another project:

> Sometimes the tail end of model chat doesn't display until the next prompt is
> entered. then it seems like nothing is happening so I repeat the prompt. Then
> I see immediately that something is being done. It seems a little out of
> order.

The text is not late. It is rendered on time and then pushed out of view by the
reader's own typing.

`#transcript` is `flex: 1` in a column with the approval bar, the totals footer
and the composer below it, so anything down there that grows takes its height
out of the scroll viewport. `growComposer` resizes the textarea on every
keystroke and adjusts nothing else, so a transcript sitting at the bottom loses
exactly as many pixels off the end as the composer gained. Clearing the box on
send gives the height back and the missing text reappears, which is what makes
it look like the next prompt is what delivered it.

The rest of the report follows from that. What appears the moment Enter is
pressed is the *previous* turn's tail, so the new turn reads as not having
started; retyping grows the composer again and swallows whatever the new turn
has produced in the meantime, and the second Enter releases all of it at once.

## Acceptance / done-when

- A transcript that was at the bottom stays at the bottom when something below
  it changes height: the composer growing under a typed prompt, the totals row
  wrapping, the approval bar appearing, the command palette opening, the window
  being resized.
- A reader who has scrolled up to re-read something is still left alone.

## Notes & worklog

Measured on the shipped page in WebKitGTK, mock mode, an 820x700 pane, with the
transcript scrolled to the bottom at the end of a turn:

| | scroll gap | #transcript | #composer |
|---|---|---|---|
| turn ends, at bottom | 0 | 585 | 69 |
| after typing an 8-line prompt | **168** | 417 | 237 |
| composer cleared on send | 0 | 585 | 69 |

The footer does the same thing on a narrower pane. At 560x460 the token totals
row wraps when the turn's `usage` event lands, the footer goes 46px to 65px and
a 19px gap opens. That one heals itself: `isAtBottom` allows 40px of slack, so
the `status` event that follows still counts as being at the bottom and
re-scrolls. Above 40px it would not, which is why the report says "sometimes".

There is an ordering fault behind the footer case as well. `applyLiveEvent`
calls `scrollToBottom` and then calls `renderPermissionModeControl`,
`renderSessionTotals` and `updateComposerState`, any of which can change the
height the scroll was just measured against.

## Resolution

One `ResizeObserver` on `#transcript`, rather than a re-scroll after each of the
things that can resize it. The composer, the footer, the approval bar and the
palette are four of them today and the list has grown once per feature; a fix
spread across all four leaves the fifth to reintroduce the bug.

Staying at the bottom is now a flag rather than a measurement. That is the whole
trick: the thing that breaks the position is a layout change, and by the time
one has happened `isAtBottom` already reads false, so an observer that measured
on the way in could never tell "the reader scrolled up" from "the box shrank
under them". The flag is set by `scrollToBottom` and refreshed from the
transcript's own scroll events, so it records what the reader last did.

### What was checked

Same harness as the measurement above, on the real page in WebKitGTK. Typing the
eight-line prompt now leaves the gap at 0 rather than 168, and the tail stays
visible while the prompt is written. A reader scrolled up 300px stays there
across a composer resize, a footer resize and a window resize.
