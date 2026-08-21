---
id: ISSUE-029
title: Display qr code for remote
status: done
type: feature
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-007, ISSUE-022]
---

## Summary

Display a qr code for the remote url so a user can scan on their phone and walk away to keep working via browser on phone

## Acceptance / done-when

- The Remote Access dialog shows a code that points a phone at a link that works from the phone.
- The code follows the token: rotating it, or opening the bind, redraws the code.

## Notes & worklog

- The link at the top of the dialog is on 127.0.0.1, which is the one link a phone cannot use.

## Resolution

The code is in the Remote Access dialog, under the two links.

Which link it encodes is the part worth explaining, and it is not the first one
in the dialog. `http://127.0.0.1:8787` resolves to the phone itself and reaches
nothing, so `_phone_url` picks in this order: the tailnet link when `tailscale
serve` is proxying the port, since it is HTTPS, needs no change to the bind, and
gives a secure context so browser notifications work on the phone
([[ISSUE-022]]); then the LAN address when the bind has been opened, captioned
with the fact that it is plain HTTP and same-network only; then nothing, with a
caption naming both ways to get a link worth scanning. A bind that is open but
has no route out falls back to loopback, and that is treated as nothing rather
than offered as a LAN link.

Encoding is python3-qrcode, already installed as a Fedora package on the same
interpreter the app runs under, so there is no pip install into a system Python.
It is declared in `pyproject.toml` and treated as optional at runtime: without
it the dialog says which package to install instead of failing to open.
`qr.py` holds the encoding and imports no GTK, which is what let the rendering
be checked on its own.

`QrCodeArea` draws the module grid with Cairo rather than scaling an image.
Modules are snapped to whole pixels, so nothing lands on a half pixel and gets
drawn as two grey ones, and runs of dark modules in a row become one rectangle.
It is dark-on-white in both themes, since inverting a QR code for a dark theme
breaks some readers. At 300px a 49-module code (a tailnet URL with a
43-character token) gets six pixels per module.

A QR code that does not scan is worth nothing, so the rendering was checked
rather than eyeballed: the widget's own draw function paints onto a Cairo
surface, and the module grid is read back out of the pixels and compared to the
matrix it was given. Identical at 240px, 300px and 121px, which rules out a
transposed grid, an off-by-one and a scale that drifts across the code. The
matrix itself is checked for a clear four-module quiet zone on all four sides
and for the 7x7 finder pattern at three corners. The dialog was then built for
real against all three link cases, including the two this machine hides because
it is already serving over Tailscale.

Not verified: no phone has scanned one. There is no QR decoder on this machine
to close that loop, so the evidence stops at "the pixels are a faithful drawing
of a well-formed matrix from a widely used encoder".

The 300px code pushed the dialog to just over 1000px tall, which would clip on a
laptop screen, so the body now scrolls and the dialog asks for 760px.
