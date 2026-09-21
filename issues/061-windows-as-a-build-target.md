---
id: ISSUE-061
title: Windows as a build target
status: open
type: feature
area: packaging
created: 2026-09-21
updated: 2026-09-21
related: [ISSUE-047, ISSUE-060]
---

## Summary

Codinian ships rpm and deb. Windows is the next target asked for.

The CLI side is already handled. Claude Code has a native Windows build:
`irm https://claude.ai/install.ps1 | iex` installs `claude.exe`, and the docs
say a native install updates itself in the background, so it is a better
default there than the copy inside the SDK for the same reason it is on Fedora.
`claude_cli.py` finds it and rejects npm's `claude.cmd` shim (ISSUE-060).

Claude Desktop is a different product. It is the chat application, it does not
install `claude.exe`, and nothing in the SDK's discovery path looks for it.
Nothing here should depend on it.

## What actually blocks a Windows build

Two of the four typelibs `window.py` requires have no Windows build. Searched
on packages.msys2.org, 2026-09-21:

| typelib | MSYS2 |
| --- | --- |
| GTK 4 | `mingw-w64-x86_64-gtk4` 4.24.0 |
| libadwaita 1 | present |
| WebKitGTK 6.0 | nothing. Only `qtwebkit` for Qt5 |
| VTE 3.91 | nothing. Only `libvterm`, which is a parser, not a widget |

Both gaps are structural rather than packaging oversights. The GTK port of
WebKit targets Linux and the BSDs; Windows has WinCairo, a separate port with
no GTK binding. VTE is a pty consumer, and a pty is a Unix object.

Those two carry the parts of the interface that matter most: every transcript
and project pane is a WebKitGTK view, and the session composer is a VTE
terminal.

## Possible directions, none costed yet

1. **Replace WebKitGTK with WebView2.** The pages are already served over HTTP
   by the app's own aiohttp server, which is how the phone reaches them, so the
   content does not need porting. Only the embedding does.
2. **Drop the embedded browser on Windows** and open the same URLs in the
   default browser, with the GTK window keeping the sidebar and the approvals.
3. **Replace VTE with ConPTY.** Windows has had a pty since 1809; what is
   missing is a GTK widget in front of it.
4. **Ship WSL instead**, which is a Linux build with a launcher, not a
   Windows build.

## Acceptance / done-when

- A decision on which of the above, with the interface it costs written down.
- A build that runs on Windows without WebKitGTK or VTE installed, since
  neither can be.
- CI builds it, the way the rpm and deb are built today.
