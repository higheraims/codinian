# Project workspace protocol

The contract for the project workspace track: ISSUE-019 (issues system) and
ISSUE-020 (project view), plus the per-repo settings file both sit on.

M1-M3 built a session manager. M4 adds a second noun. A **project** is a folder
the user has registered; it owns files, git state, an issues directory and any
sessions whose `workdir` falls inside it. Everything here is served by the same
aiohttp server and rendered by the same web bundle, so the desktop pane and the
browser get the feature at once, exactly as the transcript does.

Unlike the transcript, none of this is streamed. Projects change when the user
changes them, so these are plain REST endpoints under `/api/projects`, subject
to the same token auth, same-origin check and Tailscale identity rules as every
other `/api/` route (`docs/remote-access.md`).

## Where state lives

| What | Where | Committed to the repo? |
|---|---|---|
| The list of registered projects | `~/.local/share/codinian/projects.json` | no, it is per-machine |
| A project's own settings | `<root>/.codinian/settings.json` | yes, that is the point |
| Issues | `<root>/<issues.dir>/NNN-slug.md` | yes |

A project's settings file is versioned with the repo so issue tags, the issues
directory and the default permission mode travel with the code rather than
living in one person's home directory.

### `projects.json`

```json
{
  "version": 1,
  "projects": [
    {"id": "3f2a91c4", "path": "/home/user/Projects/codinian",
     "name": "codinian", "added_at": "2026-08-20T10:00:00"}
  ]
}
```

`id` is the first 8 hex characters of the SHA-256 of the resolved absolute path,
so it is stable across restarts and the same folder never registers twice.

### `.codinian/settings.json`

Every key is optional; a missing file means all defaults.

```json
{
  "version": 1,
  "name": "codinian",
  "default_permission_mode": "default",
  "issues": {
    "dir": "issues",
    "id_prefix": "ISSUE",
    "statuses": ["open", "in-progress", "blocked", "done", "dropped", "superseded"],
    "types": ["feature", "bug", "chore", "spec", "spike"],
    "areas": ["gui", "sdk", "remote", "db", "tools", "docs", "packaging"],
    "sections": ["Summary", "Acceptance / done-when", "Notes & worklog", "Resolution"]
  }
}
```

`statuses`, `types`, `areas` and `sections` are per-repo on purpose: ISSUE-019
asks for it, because one repo's areas are `engine` and `ui` where another's are
`db` and `networking`. The client builds its filter chips and its front-matter
dropdowns from these lists rather than from anything hard-coded.

`issues.dir` is resolved in this order: the settings value if present, else the
first of `issues/` or `docs/issues/` that exists, else `issues/` (created when
the first issue is written). ISSUE-019 standardises on repo-root `issues/`, and
ISSUE-023 moved every tracker there, so the settings value answers in practice.
The `docs/issues/` step stays because discovery is what lets the feature work in
a repo nobody has migrated, which is the case for any repo added later.

## Path safety

Every request that names a file carries a **project-root-relative** path. The
server resolves it with `realpath` and rejects anything that leaves the root,
absolute paths, and anything inside `.git/`. This is not defence against a
confused user; the server can be reachable over the tailnet, so a traversal here
is a read of any file on the machine. Reject with `400 {"error": "bad_path"}`.

## HTTP endpoints

All return JSON. Errors are `{"error": code, "detail": string}` with a 4xx/5xx
status. Shared codes: `bad_path`, `not_found`, `not_a_repo`, `git_failed`,
`too_large`, `binary`, `exists`, `invalid`.

### Projects

- `GET /api/projects` → `{"projects": [ProjectMeta, ...]}`
- `POST /api/projects` `{"path": "/abs/path"}` → `{"project": ProjectMeta}`.
  `404` if the folder does not exist, and adding an already-registered folder
  returns the existing entry rather than a duplicate.
- `DELETE /api/projects/{id}` → `{"ok": true}`. Removes the registry entry only.
  **Nothing on disk is ever deleted by this API.**
- `GET /api/projects/{id}` → `{"project": ProjectMeta, "settings": Settings,
  "git": GitInfo|null, "sessions": [SessionMeta, ...], "history": [PriorSession, ...]}`
- `PUT /api/projects/{id}/settings` `{"settings": Settings}` → `{"settings": Settings}`.
  Writes `.codinian/settings.json`, creating the directory if needed.

**PriorSession**: `{sdk_session_id, title, cwd, mtime, session_id}`. Every
`claude` conversation recorded under `~/.claude/projects` whose working
directory is inside the project, newest first, whether Codinian started it or
some other terminal did. `title` is the first line of the first real user
message. `session_id` is the live Codinian session already carrying that
conversation, or null when nothing has picked it up in this run; the client uses
it to leave adopted entries out of the history list, since they are already in
`sessions`.

**ProjectMeta**: `{id, path, name, added_at, exists, is_repo, issues_dir}`.
`exists` is false when the folder has been moved or deleted since it was
registered; the client shows such a project greyed out rather than dropping it.

### Files

- `GET /api/projects/{id}/files?path=<rel>` → `{"path": rel, "entries": [FileEntry, ...]}`
  One directory level, for a lazily expanded tree. `path` omitted or `""` means
  the root. **FileEntry**: `{name, path, is_dir, size, mtime, ignored}`.
  Directories first, then files, each case-insensitively alphabetical. Hidden
  entries are included and flagged by their leading dot in `name`. `ignored` is
  true when git ignores the path (one `git check-ignore --stdin -z` call for the
  whole listing, not one per entry); in a non-repo folder it is true for
  `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`.
  `.git` is always reported ignored, in a repo too: git excludes it itself
  rather than through an ignore rule, so `check-ignore` never names it, and
  without the special case the one directory the client may never open would
  be the only one that did not look shut.
- `GET /api/projects/{id}/file?path=<rel>` → `{"path", "content", "size", "mtime"}`.
  `413 too_large` above 512 KiB, `415 binary` when a NUL byte appears in the
  first 8000 bytes. Both carry `size` so the client can say why.
- `PUT /api/projects/{id}/file` `{"path", "content"}` → `{"path", "size", "mtime"}`.
  Writes through a temp file in the same directory and renames, so a failed
  write cannot truncate the original. Creates the file if absent; does not
  create parent directories. A path whose parent directory does not exist
  comes back as `400 bad_path` rather than `404`: the safety check has to
  resolve the parent to catch a symlink anywhere in the chain, so "your
  directory is not there" and "your path leaves the project" arrive at the
  same refusal. A client should read `bad_path` on a write as either.
- `POST /api/projects/{id}/file/new` `{"path", "kind": "file"|"dir"}` →
  `{"path"}`. `409 exists` if something is already there.
- `POST /api/projects/{id}/file/rename` `{"path", "to"}` → `{"path": to}`.
  `to` is also root-relative; `409 exists` if the destination is taken.
- `POST /api/projects/{id}/open-external` `{"path"}` → `{"ok": true}`.
  Runs `xdg-open` on the machine hosting the server. **Refused with `403
  not_local` unless the request arrives from loopback**, because "open this on
  the user's desktop" is not something a remote client should be able to ask
  for unprompted.

### Git

- `GET /api/projects/{id}/git` → **GitInfo**, or `{"is_repo": false}`:
  `{"is_repo": true, "branch", "detached", "ahead", "behind", "upstream",
    "changes": [{"path", "index", "worktree", "staged", "untracked"}],
    "tags": [...], "last_commit": {"hash", "subject", "date"} | null,
    "user": {"name", "email"} | null}`
  `changes` comes from `git status --porcelain=v1 -z`; `index` and `worktree`
  are the two raw status characters. `user` is null when the repo has no commit
  identity configured, which is what the client checks before offering Commit.
- `POST /api/projects/{id}/git/init` → GitInfo. `409 exists` if already a repo.
- `POST /api/projects/{id}/git/commit`
  `{"message", "body": string|null, "paths": [rel, ...]|null}` →
  `{"hash", "subject"}`. `message` is the subject line and `body` the longer
  explanation; they go to git as two `-m` arguments, so git puts the blank line
  between them. A body that is empty or only whitespace is omitted, and a
  request without the field at all still commits. With `paths`, stage exactly
  those and commit only them; with `paths: null`, commit all tracked
  modifications (`git commit -a`). Untracked files must be listed explicitly in
  `paths` to be included. `422 invalid` for an empty message, `422 git_failed`
  with git's stderr in `detail` for anything git refuses, including an empty
  commit.
- `POST /api/projects/{id}/git/tag` `{"name", "message": string|null}` →
  `{"tags": [...]}`. Annotated when a message is given, lightweight otherwise.
- `GET /api/projects/{id}/gitignore` → `{"content": string, "exists": bool}`
- `PUT /api/projects/{id}/gitignore` `{"content"}` → `{"ok": true}`

Every git call is `subprocess.run(["git", "-C", root, ...])` with no shell, a
15-second timeout and `check=False`. Nothing in this API rewrites history,
force-pushes, discards working-tree changes or pushes to a remote. Adding an
operation that can lose committed work needs its own issue and its own
confirmation flow.

### Issues

- `GET /api/projects/{id}/issues` → `{"issues": [IssueSummary, ...], "schema": IssuesSettings}`
  Every issue in the issues directory, newest id first. The client filters and
  sorts what it is given; there are no query parameters. `schema` is the
  resolved `issues` settings block, so the client can build its controls from
  one response.
- `GET /api/projects/{id}/issues/{issue_id}` → `{"issue": Issue}` where
  `issue_id` is the canonical token (`ISSUE-019`) or the bare number (`19`).
- `POST /api/projects/{id}/issues` `{"frontmatter": {...}, "sections": [...]}` →
  `{"issue": Issue}`. The server assigns `id`, `created` and `updated`, and
  derives the filename; a client-supplied `id` is ignored rather than honoured.
- `PUT /api/projects/{id}/issues/{issue_id}` `{"frontmatter": {...},
  "sections": [...]}` → `{"issue": Issue}`. `id` and `created` are preserved
  from the file whatever the client sends; `updated` is set to today.

**Issue**:

```json
{
  "id": "ISSUE-019", "num": 19,
  "file": "issues/019-isues-system.md",
  "frontmatter": {"id": "...", "title": "...", "status": "open", "type": "feature",
                  "area": "tools", "created": "2026-08-17", "updated": "2026-08-17",
                  "related": ["ISSUE-020"]},
  "preamble": "",
  "sections": [{"heading": "Summary", "body": "..."}, ...]
}
```

**IssueSummary** is the same without `preamble` and `sections`, plus
`"excerpt"`: the first 200 characters of the Summary section, flattened to one
line.

`created` and `updated` are ISO date strings on the wire and real `date`
objects inside the parser. That distinction is load-bearing rather than
incidental: it is what lets the serializer write a genuine date bare and quote
a string that merely looks like one, so neither changes shape on a round-trip.
The HTTP layer converts between the two.

There is no delete endpoint. The README's one rule is that an issue ID is
permanent; the API is not the place to break it. Abandoning an issue is
`status: dropped`.

### Sessions

- `POST /api/projects/{id}/sessions` `{"resume": sdk_session_id, "name": str,
  "permission_mode": str}` (every field optional) →
  `{"session": SessionMeta}`. Starts an SDK session in the project and answers
  with the session to open. With `resume`, the stored conversation is replayed
  into it first, exactly as the desktop Resume dialog does.
  - The id must be one of this project's own `history` entries, or `404
    not_found`: a project route is not a way to reopen another repo's
    conversation.
  - Resuming a conversation that already has a live session returns that
    session with `"existing": true` rather than starting a second one on top of
    it.
  - The working directory is the one the prior session recorded, which may be a
    subdirectory of the project. If that folder is gone, the project root is
    used.
  - `permission_mode` defaults to the project's `default_permission_mode`.
    An unknown mode is `422 invalid`.
  - `503 no_runtime` if the server was built without a session runtime, and
    `500 session_start_failed` if the SDK refuses to start; in the latter case
    the session stays in the list carrying its own error event.

## Issue file format

The format the README in `issues/` describes, made machine-writable.

- **Filename** `NNN-slug.md`, `NNN` zero-padded to three digits, slug from the
  title: lowercased, non-alphanumerics collapsed to single hyphens, trimmed to
  60 characters.
- **Next id** is one more than the highest number found in *either* a filename
  or an `id:` field, so a gap or a stray file can never cause a reuse.
- **Front matter** is a YAML block between `---` lines at the very start.
  Parse with `yaml.safe_load`. Do **not** serialize with `yaml.dump`: write the
  canonical keys in the order `id, title, status, type, area, created, updated,
  related`, then any other keys the file had, in their original order. Scalars
  are written bare unless they need quoting (leading/trailing space, or any of
  `:#[]{}&*!|>'"%@\``, or a value YAML would read as a number, bool or null),
  in which case double-quote and escape. Lists are inline flow style
  (`related: [ISSUE-020]`), empty list as `[]`. This keeps diffs to the fields
  that actually changed instead of rewriting every file the first time it is
  saved.
- **Body** splits on lines matching `^## `. Text before the first such heading
  is `preamble` (normally empty). Section bodies keep their original text with
  leading and trailing blank lines stripped. Unknown sections are preserved in
  place; the settings `sections` list only drives what the editor offers, never
  what the parser accepts.
- Round-tripping a file with no edits must produce byte-identical output for
  any file already in this format. That is the test. Running it over the 22
  real issues in `issues/` found two normalisations, neither of which is
  a bug: a file whose last line has no trailing newline gains one, and a YAML
  comment trailing a front-matter value is lost,
  because `yaml.safe_load` discards comments and nothing is left to re-emit
  them from. Only `_TEMPLATE.md` has such comments, and it is not an issue
  file — the parser skips it. The three real issue files that were missing a
  trailing newline have since been given one, so all 23 now round-trip
  unchanged.

## What the client renders

`remote/static/project.html`, with `project.js` and `project.css` beside the
transcript bundle, sharing `styles.css` for its colour tokens. It is a second
page rather than a mode of `index.html` so the two can be worked on
independently, and so the desktop pane can point a WebKitGTK view straight at
it.

- `/project.html?project=<id>&token=<token>` in the browser.
- `&embed=1` for the desktop pane: the same page without the topbar chrome the
  GTK window already provides.
- `&tab=files|issues|git|sessions` selects the opening tab, and the page keeps
  the current tab and the open file or issue in the URL so a reload lands back
  where it was.

Tabs:

- **Files**: lazily expanded tree; clicking a file opens it read-only, with an
  Edit toggle that turns it into a textarea and a Save that PUTs it. Ignored
  entries are dimmed. Actions for new file, new folder, rename and open
  externally.
- **Issues**: the list, filterable by status, type and area (chips built from
  `schema`) and sortable by id, updated or status; an editor with the front
  matter as real form controls (`status`/`type`/`area` as selects over the
  schema lists, `related` as a token input) and the body sections as markdown
  editors with a formatting toolbar and a live preview.
- **Git**: branch and upstream, the change list with checkboxes, a commit
  message box, tag creation, and a `.gitignore` editor. `git init` offered when
  the folder is not a repo. Commit is disabled with an explanation when
  `git.user` is null.
- **Sessions**: two lists. *Running now* is the project's live sessions with
  status and cost, each linking to `/?session=<id>` for the transcript.
  *Earlier conversations* is `history`, every `claude` session ever run in the
  folder, each with a Resume button that POSTs to the sessions route. In a
  browser, Resume then opens the new session's transcript. In the desktop pane
  (`embed=1`) it does not navigate: the pane has no way back to the project, and
  the sidebar has the new session anyway, so it says where the session went and
  refreshes the tab instead.

## Sessions and projects

Codinian keeps live sessions in memory, so a restart empties the Sessions tab
while the work itself survives in the CLI's own JSONL. The `history` list is
what closes that gap (ISSUE-024): the tab reads the same durable transcripts the
resume picker reads, filtered to the project, so a repo that has been worked in
all week looks like it.

`SessionMeta` gains `project_id`: the id of the registered project whose path is
the longest prefix of the session's `workdir`, or null when the session's
directory is not inside any registered project. It is derived on read rather
than stored, so registering a folder immediately claims the sessions already
running inside it.
