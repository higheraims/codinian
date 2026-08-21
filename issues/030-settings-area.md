---
id: ISSUE-030
title: Settings area in the sidebar
status: done
type: feature
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-007, ISSUE-019, ISSUE-029]
---

## Summary

Give the app a Settings area reached from the bottom of the sidebar, and move
Remote Access into it as one tab.

Anchored to the bottom of the left panel: the program name, the version, and a
cogwheel. Clicking the cogwheel highlights that group the way a project or
session row highlights, puts "Settings" in the content header where a project
name would go, and opens a tabbed pane in the style of the project workspace's
Files / Issues / Git / Sessions.

Remote Access moves there as-is and stops being a dialog. General starts with
theme and has room to grow.

## Acceptance / done-when

- The name, version and cogwheel sit at the bottom of the sidebar and stay there
  when the lists above them scroll.
- Selecting Settings deselects any project or session row, and vice versa.
- Remote Access works exactly as it does in the dialog today: both links, both
  copy buttons, the QR, the LAN switch, and Rotate.
- A theme choice applies to the GTK shell and to the WebKitGTK panes together.
- The version shown comes from one place that `pyproject.toml` also reads.

## Notes & worklog

- Decided up front: the pane is native GTK, not a web page, and it owns the
  global config only. Reasons in the two sections below.
- The server icon in the header was dropped rather than repointed at Settings.

## Design decisions

**Native GTK, not an HTML page.** Every M4 feature was built as a web page so
the browser client got it at the same time. Settings deliberately breaks that
pattern, for a reason specific to what it holds: the Remote Access tab shows the
access token, rotates it, and switches the bind between loopback and every
interface. As a web page, a token holder on the tailnet could open this
machine's port to the whole local network, which is an escalation that does not
exist today. Keeping it native keeps that control on the machine it affects.

libadwaita 1.9.3 is installed, which has `Adw.InlineViewSwitcher` (1.7+). That
is a flat underlined tab strip and lands close to `.tab-nav`/`.tab-btn` in
`project.css` without hand-drawing anything.

**Global config only.** Two stores answer to the word "settings":
`~/.config/codinian/config.json` (token, bind, port,
`trust_tailscale_identity`) and each repo's `.codinian/settings.json` (issues
directory, statuses/types/areas, `default_permission_mode`). This area owns the
first. The second belongs beside the repo it configures, as a fifth tab in the
project workspace, and is out of scope here.

## What this turns up

Things worth deciding before or during the work, found while reading the code
for it.

**The version has no runtime source.** `pyproject.toml` says `0.1.0`,
`__init__.py` is empty, and `importlib.metadata.version("codinian")` raises
`PackageNotFoundError` because the app runs from source rather than installed.
Fix: `__version__` in `__init__.py`, with `pyproject.toml` taking
`dynamic = ["version"]` from it, so the sidebar and the package agree and there
is one place to bump.

**"Theme" is two themes.** The GTK shell follows the system through libadwaita;
the transcript and project panes are WebKitGTK pages that follow
`prefers-color-scheme` in `styles.css`. Setting one alone gives a light
transcript inside a dark window. Two pieces of work:

- Shell: `Adw.StyleManager.get_default().set_color_scheme(...)`, persisted.
- Pages: `styles.css` currently defines the light palette on `:root` and the
  dark one only inside `@media (prefers-color-scheme: dark)`, so there is no way
  to force either. It needs a `data-theme` hook, with the media query kept for
  the "follow system" case. `project.css` defines no palette of its own and
  inherits, so it needs nothing. The panes then have to be told the choice, by
  query parameter on the pane URL or by reloading them when it changes.

**`trust_tailscale_identity` has no UI at all.** It is a security-relevant key
that can only be set by hand-editing `config.json` today. The Remote Access tab
is where it belongs, with the warning from `docs/remote-access.md` next to it:
a proxied request and a forged local one are indistinguishable at the socket.

**Notifications cannot be turned off.** `window.py:_notify_status` fires a
desktop notification on a pending approval and on a finished turn, with no
setting anywhere. That is the obvious second thing in General after theme.

**A global default permission mode.** New sessions take their mode from the
repo's `.codinian/settings.json`, falling back to `default` in code. A global
fallback for folders that are not registered projects belongs in General.

**Selection needs a third participant.** `_on_row_selected` and
`_on_project_row_selected` already cross-deselect two `Gtk.ListBox`es. Making
the bottom group a one-row `Gtk.ListBox` with the `navigation-sidebar` class
gets the highlight the user asked for at no cost and keeps all three
coordinating through the same pattern.

**The QR gets its space back.** ISSUE-029 sized the code for a 520px dialog and
added a `Gtk.ScrolledWindow` plus a 760px height to stop it clipping. In a
full-width pane neither is needed. Decide whether `RemoteAccessDialog` is
deleted outright or kept as a thin wrapper around the same widgets; the header
bar's server-icon button should then select Settings on the Remote access tab
rather than opening a dialog, which is the deep-link pattern `project.html`
already has through `&tab=`.

**About wants somewhere to live.** Version, application id, and the paths of the
config file and the projects registry are the things asked for when something is
wrong. A third tab, or the tail of General.

## Plan

1. `__version__` in `__init__.py`; `pyproject.toml` reads it dynamically.
2. Sidebar: move the two lists into a scrolled area that ends above a pinned
   bottom group holding name, version and cogwheel, built as a one-row
   `Gtk.ListBox` so it highlights like the others. Cross-deselect all three.
3. Settings pane: an `Adw.ViewStack` with `Adw.InlineViewSwitcher`, added to
   `_stack` as `settings`, with `_show_settings` setting the header title.
4. Move the Remote Access groups out of `remote_dialog.py` into the Remote
   access tab unchanged, dropping the dialog's scroller and height. Add
   `trust_tailscale_identity` there.
5. General tab: theme, then notifications and the default permission mode.
6. Theme plumbing: `Adw.StyleManager` for the shell, a `data-theme` hook in
   `styles.css`, and the panes told which to use.
7. Repoint the header's server-icon button at Settings on the Remote access tab.
8. Record the browser-parity exception in `docs/HANDOFF.md` and the theme
   contract in `docs/transcript-protocol.md`.

## Resolution

Built as planned, with two additions the plan did not anticipate and one
correction to it.

The sidebar's scrolling lists now sit above a pinned group: an `Adw.ActionRow`
titled "Codinian" with the version as its subtitle and a cogwheel suffix, inside
a one-row `Gtk.ListBox` carrying `navigation-sidebar`. That gets the selected
highlight for free and makes three lists that must cross-deselect, which
`_clear_other_selections` does behind a re-entrancy guard, since `unselect_all`
fires `row-selected` with None and would otherwise call back into the handler
that asked for it. Measured on the built window: the group occupies y=758 to 820
in an 820px window, with the project list ending above it.

The pane is an `Adw.ViewStack` under an `Adw.InlineViewSwitcher`, 288px wide
with the three tabs General, Remote access and About. It is built on first
selection rather than at startup, unlike the session and project panes: most
runs never open it, and constructing it shells out to `tailscale serve status`.
Reopening it refreshes the Remote access tab, so a tailnet link set up while the
app was running appears without a restart.

`RemoteAccessDialog` became `RemoteAccessPage`, an `Adw.PreferencesPage` in
`remote_panel.py`. A PreferencesPage scrolls its own groups at a clamped width,
so the `Gtk.ScrolledWindow` and the 760px height that ISSUE-029 needed to stop
the QR clipping a 520px dialog are both gone. Toasts were the one thing that did
not move unchanged: a page inside a stack has nowhere to put one, so the page
emits a `toast` signal and the window, which now wraps its split view in an
`Adw.ToastOverlay`, shows it. That is better placed anyway, since a toast should
outlive switching tabs.

The server icon is gone from the header rather than repointed at the new tab.

### Theme

`theme.py` owns the choice and gives each half of the interface what it needs:
`Adw.StyleManager` gets FORCE_LIGHT or FORCE_DARK rather than PREFER_*, since
the user chose rather than hinted, and the pane URLs get a `theme=` parameter.
"Follow the desktop" appends nothing, so the common case produces the URL it
always did and the page falls through to `prefers-color-scheme`.

`styles.css` could not express a forced theme at all: the light palette was on
`:root` and the dark one only inside the media query. It now carries the dark
palette twice, under the media query and under `[data-theme="dark"]`, with the
media query guarded by `:not([data-theme="light"])` so choosing light on a dark
desktop actually gives light. The two dark blocks are checked against each other
by parsing both and comparing the 27 declarations, since a hand-maintained
duplicate drifts. A script in each page's `<head>` stamps the attribute before
first paint, so no page shows the wrong palette and then corrects itself.
`project.js` needed one more change: `syncUrl` rebuilds the query string from
scratch and dropped the parameter on the first `replaceState`.

A WebKitGTK pane reads that attribute only at load, so changing the theme
reloads every pane. `_reload_panes` is shared with token rotation, which had the
same problem for a different reason.

Verified in a real browser across the whole matrix, choice against desktop
preference, using `Emulation.setEmulatedMedia`: following the desktop tracks it
both ways, and an explicit choice wins both ways, including light-on-dark, which
is the cell the guard exists for.

### The correction

The plan said a global default permission mode would be the fallback when a
project has none. `project.load_settings` merges `DEFAULT_SETTINGS`, so
`default_permission_mode` is never absent and a repo that never expressed a
preference is indistinguishable from one that chose `default`. Written as
planned, every project would silently override the global choice.
`project.settings_override` reads the raw file for one key and returns None when
the file does not mention it, which is the distinction the fallback needs.

### Also picked up

`trust_tailscale_identity` now has a switch on the Remote access tab; it was
config-file-only before. Desktop notifications have an off switch, checked after
the last-status bookkeeping so turning them back on does not announce something
that happened while they were off. `config.APP_ID` replaced the application id
that `main.py` and the About tab would otherwise have spelled out separately.
`version.py` is the single source the sidebar, the About tab and
`pyproject.toml` all read.

### What was checked

Logic without a display: theme persistence and fallback for an unknown value,
the new config defaults against a config file written before this issue, and
`settings_override` across a missing file, a file that omits the key, a file
that sets it, and a corrupt file. On a built window: the bottom group's contents
and pinning, lazy construction, the header title, the three tabs and their
labels, `show_tab` including an unknown name, three-way deselection in every
direction, and the theme reaching both pane URLs. Then the real app under its
own config and application id, which comes up with FORCE_DARK and a pane URL
carrying `&theme=dark`.

Glyphs do not render in an offscreen GTK snapshot and a window at `opacity: 0`
produces no render node, so the visual result was measured through
`compute_bounds` rather than eyeballed in a picture. The tab strip and the
pinned group should still get a human look.
