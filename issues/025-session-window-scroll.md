---
id: ISSUE-025
title: Session window does not scroll
status: done
type: bug
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-026, ISSUE-027, ISSUE-028]
---

## Summary

Session window in app does not scroll initially - it just keeps compressing vertically

## Acceptance / done-when

- Allow the session content to scroll as it expands larger than the window. Buttons to approve actions become collapsed out of view. I solved it in the working session where this was noticed by doing approvals by the app's remote feature.
- there may be more nuance to this as it eventually starts scrolling but not until the view is impossibly cramped. 

## Notes & worklog

- - See screenshots in Docs folder

## Resolution

The nuance in the second bullet was the diagnosis. `#transcript` is a flex
column with `overflow-y: auto`, so every entry in it is a flex item carrying the
default `flex-shrink: 1`. Three of the entry types set `overflow: hidden`:
`.tool-card`, `.approval-card` and `.thinking-block`. That is the exact
condition under which a flex item's automatic minimum size stops being its
content height and becomes zero, so the column had permission to squeeze those
entries to nothing. Scrolling only began once everything shrinkable had already
been crushed, which is why it started "not until the view is impossibly
cramped".

Measured in headless Chromium against the page itself, 20 tool cards and 20
resolved approval lines in one transcript: the tool cards rendered 2px tall,
while the resolved lines held 36px because `.approval-resolved-line` sets no
`overflow` rule and kept `min-height: auto`. `scrollHeight` came back equal to
`clientHeight`. Those two heights side by side are the screenshots: full-height
"Bash: Approved" rows separated by hairlines that are the tool cards.

The fix is one rule, `#transcript > * { flex-shrink: 0; }`. Same measurement
after it: tool card 150px, `scrollHeight` 4516 against a 696px viewport.
`.approval-card` being on the crushed list is also why the Approve and Deny
buttons disappeared, so that half of the report closes with the same line.

Approvals could still go unnoticed once the transcript is long, since the pane
auto-scrolls only when it was already at the bottom. So `#approval-jump` sits
between the transcript and the composer: a bar that appears only while a
`approval_request` with no `approval_resolved` is off screen, and scrolls it to
the middle of the view on click. Verified on the real page against the real
server: hidden with the card in view, shown after scrolling to the top, and
after clicking it the Approve button is inside the viewport and the bar has
hidden itself again.
