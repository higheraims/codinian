# Skills

Packaged skills for Claude Code, kept in the repo so they are versioned with the
project that uses them.

Each skill is a directory holding `SKILL.md` (with the frontmatter that decides
when it triggers) plus optional `scripts/` and `references/`. The `.skill` file
beside it is that directory zipped, which is the installable form.

The directory is the source of truth; the `.skill` file is a build artifact,
not tracked in git. Build one when you want the installable form:

```
cd skills && zip -r delegating-to-subagents.skill delegating-to-subagents
```

| Skill | What it covers |
|-------|----------------|
| `agpl-code-compliance` | License headers, dependency licenses, duplication and AI-disclosure trailers before an AGPLv3 contribution. |
| `delegating-to-subagents` | Splitting work across cheaper agents and verifying what comes back, including a Chrome DevTools Protocol driver for browser UI. |
| `no-ai-slop` | Rules and worked examples for prose that does not read like AI slop. Derived from Louis Rossmann's [no_ai_slop_writing_rules](https://github.com/realrossmanngroup/no_ai_slop_writing_rules). |
| `web-browse` | Fetching a page that plain HTTP cannot reach: bot-check stubs, JavaScript-gated pages, Anubis or Cloudflare challenges. Drives headless Firefox over Marionette. |
