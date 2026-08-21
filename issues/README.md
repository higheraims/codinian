# Issues

The project's issue tracker, in-repo and versioned. One Markdown file per issue, plain
text, works offline, readable in any editor. Borrowed from an earlier project's
tracker, same rules.

## The one rule that matters

**An issue's ID is permanent. It is assigned once, never reused, never renumbered.**

That is the whole point of this directory. A tracker whose numbers shift means a code
comment saying `ISSUE-004` resolves to different work depending on when it was written.
Stable IDs make a reference in code, a commit message, or a memory note resolve forever.

## Format

- **File:** `NNN-slug.md`. `NNN` is the zero-padded canonical number; the slug is for
  humans and may be tidied without consequence (the ID in the frontmatter is the source of
  truth). Example: `005-websocket-event-push.md`.
- **Canonical ID / reference token:** `ISSUE-NNN` (e.g. `ISSUE-005`). Use this token in
  code comments, tests, docs, and commit messages; it is greppable and never collides with
  hex colours or GitHub `#refs`.
- **Frontmatter** (YAML):

  | field | values |
  |---|---|
  | `id` | `ISSUE-NNN` |
  | `title` | one line |
  | `status` | `open` · `in-progress` · `blocked` · `done` · `dropped` · `superseded` |
  | `type` | `feature` · `bug` · `chore` · `spec` · `spike` |
  | `area` | `gui` · `sdk` · `remote` · `db` · `tools` · `docs` · `packaging` |
  | `created` / `updated` | `YYYY-MM-DD` |
  | `related` | list of `ISSUE-NNN` |

- **Body:** opens directly at `## Summary` (the frontmatter is the single source of truth
  for id/title/status/type/area, with no in-body title heading or status row to drift), then
  `## Acceptance / done-when`, `## Notes & worklog`, and `## Resolution` once done. Link
  issues inline as `[[ISSUE-NNN]]`.

## Adding a new issue

1. Copy [`_TEMPLATE.md`](_TEMPLATE.md) to `NNN-slug.md`, where `NNN` is **one more than the
   highest existing number** (`ls issues | grep -Eo '^[0-9]+' | sort -n | tail -1`).
2. Fill in the frontmatter and Summary. Set `status: open`.
3. Reference it from code and commits as `ISSUE-NNN`.

Never renumber an existing file. If an issue is abandoned, set `status: dropped` and keep
the file, so old references still resolve. If a better-scoped issue replaces it, set
`status: superseded` and link the replacement in `related`.

## Listing / querying

No tooling and no separate status board to drift: the per-file `status:` is the source of
truth. Query with grep:

```sh
# open issues, by file
grep -l '^status: open$' issues/*.md

# a one-line board
for f in issues/[0-9]*.md; do
  printf '%s  %s  %s\n' \
    "$(sed -n 's/^id: //p' "$f")" \
    "$(sed -n 's/^status: //p' "$f")" \
    "$(sed -n 's/^title: //p' "$f")"
done
```

## Where the first issues came from

[[ISSUE-002]] through [[ISSUE-017]] are the milestones and risks written up in
[../pivot-plan.md](../pivot-plan.md), split one issue per separable piece of work. The
pivot plan stays as the narrative: why the project is moving from VTE terminals to the
Agent SDK, and how the pieces fit. These files track the state of each piece.
