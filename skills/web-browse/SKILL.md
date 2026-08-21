---
name: web-browse
description: Fetch a page through a real headless Firefox instead of a plain HTTP fetch. Reach for this when WebFetch (or curl) comes back with a bot-check or challenge stub instead of the real page, text like "Making sure you're not a bot", "Just a moment...", "Checking your browser", "Verifying you are human", "Protected by Anubis", or any interstitial that clearly isn't the content you asked for. Also use it when a page is known to render its content with JavaScript (an empty or near-empty body from a plain fetch), or when the site is known to sit behind Anubis, Cloudflare, or a similar challenge (bugs.winehq.org and gitlab.winehq.org are two that do). Also covers running a web search (DuckDuckGo, Google, or Bing) and getting back the results page's links. Do not reach for this as a first resort; see "When to use this" below.
---

# web-browse

Drives a throwaway headless Firefox over its built-in Marionette protocol and
prints the page as text, DOM, or a list of links. No geckodriver, no
selenium, no pip install: just Firefox and this script.

## When to use this

**WebFetch first, always.** This is the fallback for when WebFetch fails in
a specific way, not a faster or better alternative to it. A cold profile
plus the default page-settle wait costs on the order of **25 seconds**
before this script prints anything. Reach for it only after WebFetch has
actually returned one of:

- A bot-check or challenge stub: "Making sure you're not a bot", "Just a
  moment...", "Checking your browser", "Verifying you are human", "Protected
  by Anubis", or similar.
- A page that is known to need JavaScript to render its real content (the
  plain fetch came back near-empty).
- A site known to sit behind Anubis or Cloudflare (for example
  `bugs.winehq.org`, `gitlab.winehq.org`).

If WebFetch already returned usable content, stop. There is nothing this
script does better for you.

## Requirements

- **Firefox must be on PATH.** On this host it is at `/usr/bin/firefox` and
  is already on PATH in a plain host terminal.
- If you are running inside the sandboxed VSCodium/flatpak container (check
  with `test -f /.flatpak-info`), host binaries are not on PATH from inside
  the container. Either run this script through `flatpak-spawn --host`, or
  from a plain host terminal, not from inside the container directly.
- Nothing to install: Marionette ships inside Firefox itself.

## Usage

```
python3 scripts/browse.py fetch  URL
python3 scripts/browse.py search 'some query'
python3 scripts/browse.py fetch  URL --html          # DOM instead of rendered text
python3 scripts/browse.py fetch  URL --links         # every link on the page
python3 scripts/browse.py fetch  URL --wait 30       # seconds to let the page settle (default 25)
python3 scripts/browse.py search 'some query' --engine google   # duckduckgo (default), google, or bing
```

`fetch` prints the page's rendered text by default. Pass `--html` for the
raw DOM, or `--links` for every link on the page as `link text -> URL`.

`search` runs the query against a search engine and renders the results
page. **It returns links, not answers.** If you pass neither `--html` nor
`--links`, `search` sets `--links` for you, because a results page is only
useful as the list of links on it. Read the rendered text of a search
results page and you get navigation chrome and ads, not the ranked list.
Pass `--html` explicitly if you actually want the raw DOM instead.

## Concurrency

Each run picks its own Marionette port, an OS-assigned free ephemeral port,
unless `BROWSE_MARIONETTE_PORT` is set to force a specific one, so
concurrent invocations never fight over one fixed port. If Firefox exits
right after opening that port (a very unlucky race, not the ordinary case),
the run fails with an explicit error rather than silently attaching to
whatever else is listening there.

## Why this exists

`bugs.winehq.org` and `gitlab.winehq.org` sit behind Anubis, which demands a
JavaScript proof-of-work before serving anything. Plain fetchers get the
challenge stub, not the page. This script drives a real browser through
Firefox's Marionette protocol, which solves the challenge and waits out the
handful of interstitial documents (challenge page, "Success!" page,
redirect) that a plain fetch cannot see past, before returning the page that
was actually asked for.
