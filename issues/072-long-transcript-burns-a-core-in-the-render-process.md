---
id: ISSUE-072
title: A long transcript burns a core in the render process whenever a CSS animation is running
status: open
type: bug
area: remote
created: 2026-09-24
updated: 2026-09-24
related: []
---

## Summary

The WebKit render process behind a session pane burns most of a core
continuously once the transcript is long, with nothing arriving and nobody
touching the window. Measured against the pane for a MyCal session on
2026-09-24: 63 minutes of CPU over 1h45m of wall clock, sitting at 138% of a
core when this was written.

Most of it is on the ThreadedCompositor thread, so it is repaint rather than
script or layout. One 3-second sample of the running pane:

```
1044 jiffies  ThreadedCompositor
 545 jiffies  WebKitWebProcess (main)
   6 jiffies  ReceiveQueue
```

An earlier sample of the same process read 777 against 1, with the main thread
idle.

What that pane held:

```
8,488 events
33,534 DOM nodes
1,210 tool cards, 289 text blocks
48,939px scroll height against a 600px viewport
5.6 MB of document HTML
```

## Steps to reproduce

Open a pane on a transcript of a few thousand events and watch the render
process. Reproduced outside the app by pointing a standalone WebKitGTK view at
the same pane URL and swapping one stylesheet inside a single page load:

```
49.6% cpu   animations on (untouched)
61.2% cpu   .dot{animation:none !important}
60.9% cpu   animations on
-0.2% cpu   *,*::before,*::after{animation:none !important}
53.1% cpu   animations on
 0.1% cpu   all off again
```

Trimming the transcript to six top-level children with animations still
running: 5.1%. The repainted area tracks the size of the whole transcript, not
the size of the animated element.

That replica ran under Broadway with no GL, where the work lands on the main
WebKit thread instead of a compositor thread, so its absolute numbers are not
the app's. The on/off ratio is the part that carries over.

## Not yet isolated

Which animation. `.dot{animation:none !important}` on its own changes nothing,
and the only elements reporting a running animation by computed style were
three `span.dot.dot-working`. So either the win from the `*` rule comes from the
full restyle it forces rather than from stopping an animation, or something
animates that a computed-style sweep does not see.

The two candidates in `styles.css` are `pulse` on `.dot-working` and
`.dot-awaiting_approval` (line 576) and `pulse` on
`.text-block.is-streaming::after` (line 1647).

`document.getAnimations()` returns an empty list in this WebKitGTK even where
computed style reports `animation-name: pulse`, so it cannot answer the
question.

## Acceptance / done-when

- A pane on a transcript of several thousand events sits near zero CPU when
  nothing is arriving.
- The fix is verified by measuring the render process, not by reading the CSS.

## Notes & worklog

- 2026-09-24: Found while investigating a session whose end-of-turn text was
  arriving one character at a time. That turned out to be unrelated. Generation
  upstream had dropped to about 2 tokens a minute, both live sessions were
  affected at once, and both CLI processes were asleep in `ep_poll` with empty
  socket receive queues. This burn is real, but it does not slow generation and
  it does not delay text reaching the page.

- Two measurements taken along the way reported 0.0% because they were reading
  a bwrap wrapper rather than the render process. `pgrep -f WebKitWebProcess`
  matches the two bwrap parents as well; the render process is the one with a
  `ReceiveQueue` thread.

- Comparing separate runs does not work here. Baseline drifted 65%, then 56%,
  then 39% across three runs while the real app competed for the CPU. Every
  number above comes from toggling inside one page load instead.
