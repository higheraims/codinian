---
id: ISSUE-018
title: Ship tools/browse.py as a web-fetch capability sessions can find
status: done
type: feature
area: tools
created: 2026-08-16
updated: 2026-08-20
related: [ISSUE-004, ISSUE-014, ISSUE-016]
---

## Summary

`tools/browse.py` (236 lines) drives headless Firefox over Marionette and prints a page as
text, DOM, or a list of links, plus a `search` subcommand that renders a DuckDuckGo, Google,
or Bing results page. It needs no geckodriver, no selenium, and no pip install. Marionette
is built into Firefox, and the script speaks its length-prefixed JSON protocol directly over
a socket. It runs against a throwaway profile, so it never touches the profile you browse
with and does not disturb a Firefox you already have open.

It exists because the built-in `WebFetch` cannot get past a JavaScript wall. `bugs.winehq.org`
and `gitlab.winehq.org` sit behind Anubis, which demands a proof-of-work before serving
anything; plain HTTP fetchers get a "Making sure you're not a bot!" stub. A real browser
solves the challenge and gets the page. The script also waits out interstitials that plain
fetching cannot see through: it polls for `document.readyState`, for a body over 40
characters, and against a list of interstitial markers ("verifying", "just a moment",
"protected by anubis"), because a challenge page unloads the document several times before
the real one arrives.

**The problem is discovery, not capability.** Nothing in this repository references the
script. `grep -rn "browse" --include='*.py'` finds nothing outside the file itself; the
README does not mention it; no skill points at it. An agent working in this project hits a
bot-check stub, has no idea a working fetcher sits two directories away, and reports that
the page cannot be retrieved. A tool nobody can find is not a capability.

## The question: feature, capability, or skill?

Four ways to fix discovery, with what each actually buys:

**A. Document it in the README.** One paragraph, no code. Helps a human who reads the
README. Does nothing for a model mid-task, which is exactly the case that fails today.

**B. Package it as a skill.** `skills/` already holds `agpl-code-compliance.skill` (a zipped
`SKILL.md` plus `scripts/`), so the convention exists. A `web-browse` skill bundles the
script with a `SKILL.md` whose description names the trigger: a fetch returned a bot-check
or challenge stub, a page needs JavaScript to render, or a site is behind Anubis or
Cloudflare. Skill descriptions are matched against the task, so the model reaches for it at
the moment of failure rather than needing to know it exists. Works today, in any Claude Code
session, with no changes to codinian itself.

**C. Expose it as an SDK tool.** Once [[ISSUE-004]] drives sessions through the Agent SDK,
codinian can register `browse` as an in-process MCP tool, so every session it starts has it
in the tool list without any skill loading. Cleaner ergonomics, structured arguments,
approval flows through the same `canUseTool` path as everything else. It only helps sessions
codinian starts, and it cannot ship before the SDK driver does.

**D. A GUI feature.** A pane in the desktop app for fetching a URL. That serves a human who
wants to read a page, which is what a browser is for. It does not serve the case in the
Summary.

**Recommendation: B now, C once [[ISSUE-004]] lands, and skip D.** B and C are not rivals:
they share one script and one set of arguments, and C is a thin registration over the same
code. B removes the failure today and keeps working outside codinian; C makes it native to
sessions codinian owns.

## Known limitations to handle before packaging

- **The Marionette port is fixed.** `PORT` defaults to 2829 (`BROWSE_MARIONETTE_PORT`
  overrides it). Two concurrent invocations collide, and the failure is quiet rather than
  loud: the second Firefox cannot bind the port, but `launch()` polls until *something*
  answers on 2829, connects to the first browser, and drives the wrong one. Codinian runs
  many sessions at once, so this is the first thing to fix. Pick a free port per run, or
  pass a distinct one per session.
- **Firefox must be on PATH.** Present on this host at `/usr/bin/firefox`. Inside the
  flatpak/toolbox VSCodium container it is not, and host binaries need
  `flatpak-spawn --host`. The skill has to say which environment it is being run from
  rather than reporting "firefox is not on PATH" and stopping.
- **It is slow by design.** A cold profile plus a page settle defaults to 25 seconds of
  waiting. This is the fallback for pages that resist plain fetching, not the default path.
  Say so in the skill, or it will get used for pages `WebFetch` would have returned in a
  second.
- **`search` returns links, not answers.** With neither `--html` nor `--links`, the search
  subcommand sets `--links` itself, because a results page is only useful as its links. Worth
  stating, since the output shape differs from `fetch`.

## Acceptance / done-when

- `skills/web-browse/` exists with a `SKILL.md` whose description triggers on bot-check
  stubs, JavaScript-gated pages, and Anubis or Cloudflare challenges, bundling the script
  under `scripts/`.
- Concurrent invocations no longer collide on a fixed Marionette port, and a run that cannot
  get its own browser fails with a clear message instead of driving another run's.
- The skill states the host-versus-container requirement and the cost of a cold launch.
- `docs/README.md` mentions the capability and points at the skill.
- A follow-up note here records whether the script stays in `tools/` with the skill
  referencing it, or moves into the skill bundle as the single copy. Two copies drifting is
  the outcome to avoid.

## Notes & worklog

- 2026-08-16: Filed. The script currently works; nothing here is a bug report against its
  fetching, only against how findable and how parallel-safe it is.
- Once [[ISSUE-016]] lists available skills in the GUI, this is one of the entries that
  makes that list worth having.
- 2026-08-20: Implemented option B. `skills/web-browse/SKILL.md` bundles the script under
  `skills/web-browse/scripts/browse.py`, with a description that names the trigger
  conditions (bot-check stubs, "just a moment", JavaScript-gated pages, Anubis/Cloudflare)
  and states up front that it is a fallback, not the default path.
  - **One copy, not two.** `grep -rn "tools/browse" . --include='*.py' --include='*.md'
    --include='*.json'` found nothing outside the file itself and this issue doc, so
    `tools/browse.py` moved into the skill as the single copy and was deleted from `tools/`
    rather than kept as a second copy that could drift.
  - **Port fix.** The fixed `PORT = 2829` default is gone. `pick_port()` now binds an
    ephemeral port (socket to `("127.0.0.1", 0)`, read `getsockname()[1]`, close), unless
    `BROWSE_MARIONETTE_PORT` is set, in which case that value is used as before. Each call to
    `render()` calls `pick_port()` and threads the chosen port through `launch()` and
    `Marionette()` as a plain argument, so there is no shared global state a second
    concurrent process could race against. `launch()` also now checks `proc.poll() is None`
    a second time right after the port answers, and raises with the child's pid and exit
    code if Firefox died in that instant, rather than silently trusting whatever is
    listening on the port.
  - **Deviation from the plan worth flagging:** the issue write-up (and my task brief)
    described passing the port to Firefox via a `--marionette-port` CLI flag. Firefox 153 on
    this host has no such flag (`firefox --help` lists only `--marionette`, which just turns
    the server on). The port is still set the way the original script already did it and the
    way Firefox actually supports: the `marionette.port` pref written into the profile's
    `user.js`, plus the `MOZ_MARIONETTE_PORT` environment variable as a second path to the
    same value. Only the *value* is now per-run instead of a shared default.
  - **Verification.** Ran a single `fetch https://example.com` (succeeded). Then started two
    local `python3 -m http.server` instances on 127.0.0.1:8931 and :8932 serving two HTML
    files with distinct marker strings, and launched `browse.py fetch` against both at the
    same wall-clock moment as backgrounded shell jobs; each run returned only its own page's
    marker text, with no cross-talk, wrong-page content, or hang. Repeated with four
    concurrent fetches (ports 8931-8934) with the same result: four distinct pages back,
    correctly matched. Also called `pick_port()` directly to confirm it returns a fresh OS
    port by default and honors `BROWSE_MARIONETTE_PORT` when set.
  - **Could not verify:** the original *bug* (silently attaching to another run's browser on
    a shared fixed port) was not reproduced directly. The fix removes the shared fixed port
    entirely, so there was no longer a way to force the old collision against the new code to
    confirm the old symptom. Confidence in the fix rests on the concurrency test above (every
    concurrent run got its own page) plus reading through the changed `launch()`/`pick_port()`
    logic, not on reproducing the original failure and watching it disappear.
  - `docs/README.md` gained a section pointing at the skill. `skills/README.md` was left
    untouched (out of the scope fence for this task), so its skill table does not yet list
    `web-browse`; a `.skill` zip of the bundle was likewise not built, since the zip lives
    beside the directory rather than inside `skills/web-browse/**`.

- **2026-08-20, reviewed on return.** Three things the delegated work left, all
  now closed: `skills/README.md` had a skill table that did not list
  `web-browse`, which is the discovery problem this issue exists to fix;
  `web-browse.skill` was not built, though that README documents the zip step
  and the two other skills have one; and `tools/` was left as an empty
  directory, now removed.

  Two claims checked independently rather than taken on report. `firefox --help`
  on this host lists `--marionette` as a boolean with no `--marionette-port`, so
  the deviation to the `marionette.port` profile pref plus `MOZ_MARIONETTE_PORT`
  was correct. And a fresh concurrency run against two local servers returned
  each its own marker with both exit codes zero.

  **Residual race, accepted.** `pick_port()` binds port 0, reads the assigned
  port and closes the socket, so there is a window in which another process
  could take that port before Firefox binds it. This is the standard ephemeral
  reservation race and is not fully closable from outside Firefox. It degrades
  safely rather than silently: `launch()` re-checks `proc.poll()` after the port
  answers and raises with the child's pid and exit code, which is the loud
  failure this issue asked for instead of driving another run's browser.
