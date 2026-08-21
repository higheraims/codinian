# Transcript protocol

One event model, produced once in the backend, rendered by two clients: the
WebKitGTK pane in the desktop app and the browser. This document is the contract
between `SessionManager`'s event bus (ISSUE-003), the SDK driver (ISSUE-004), the
WebSocket server (ISSUE-005), and the shared renderer (ISSUE-005). Change it here
first, then change code.

## TranscriptEvent

Every event is a JSON object with these common fields:

| field | type | notes |
|---|---|---|
| `type` | string | one of the types below |
| `session_id` | string | the codinian session id (8-char), not the SDK's uuid |
| `seq` | integer | monotonic per session, starts at 0, no gaps |
| `ts` | string | ISO-8601 timestamp |

The `seq` is the ordering and de-duplication key. A client that has seen `seq`
up to N ignores anything `<= N`. Backlog replay (below) uses it too.

### Types and their extra fields

- **`system`** `{ subtype: "init"|"error"|"note", data: object }`
  Session lifecycle. `init.data` carries `model`, `cwd`, `sdk_session_id`, and the
  tool list. `error.data` carries `message`.

- **`text`** `{ role: "assistant"|"user", text: string, source?: "operator"|"injected" }`
  A finished text block. (M1 sends whole blocks; streaming deltas are a later
  enhancement, same type with a `partial: true` flag.)

  `source` qualifies a `user` block. `operator` is what the user typed into
  Codinian; it is emitted when the message is queued, since the SDK does not
  stream our own prompts back. `briefing` is a subagent's opening turn, the task
  its parent handed it. `injected` is a user turn the CLI produced
  itself -- a skill body, a hook's output, a system reminder -- which the
  renderer folds away rather than showing as conversation (ISSUE-026). Absent
  on assistant blocks and on events recorded before the field existed.

- **`thinking`** `{ text: string }`
  Extended-thinking block. Render collapsed by default. On current models the
  text is empty unless the session asked for `thinking.display: "summarized"`;
  see the Claude tab in Settings (ISSUE-032).

- **`tool_use`** `{ tool_use_id: string, name: string, input: object }`
  Claude is calling a tool. `name` is e.g. `Bash`, `Read`, `Edit`, `Write`.
  `Agent` is the one that spawns a subagent; see `parent_tool_use_id` below.

- **`tool_result`** `{ tool_use_id: string, is_error: boolean|null, content: string|array }`
  The result for a prior `tool_use` with the same `tool_use_id`. `content` is
  either a string or an array of blocks. A block is usually `{type: "text",
  text}`, but a tool that returns a picture -- `Read` on a PNG, a screenshot
  driver -- sends `{type: "image", source: {type: "base64", media_type, data}}`
  instead, where `data` is the image itself in base64. Render those as images:
  stringifying one puts 60,000 characters of base64 where the picture should be,
  which is what both clients did until ISSUE-035. See that issue for the size
  this costs on the wire.

  A result closing an `Agent` call additionally carries `agent_id`, plus
  `agent_description`, `agent_model` and `agent_status` when the transcript
  recorded them. This only appears on replayed history: a live session streams
  the subagent's blocks instead. The client uses `agent_id` to fetch that
  subagent's transcript on demand (ISSUE-017).

- **`permission_note`** `{ tool_use_id: string, name: string, mode: string, outcome: "allow"|"defer" }`
  A tool call the session's permission mode let through without asking, so no
  `approval_request` was raised for it. `outcome` is `allow` when Codinian
  approved it on the mode's behalf (`bypassPermissions`, or `acceptEdits` on an
  edit tool) and `defer` when the decision was left to the CLI (`auto`,
  `dontAsk`). Render as a one-line note: a session that has stopped prompting
  should say which mode did that (ISSUE-027).

- **`approval_request`** `{ request_id: string, tool_use_id: string, name: string, input: object }`
  Claude wants to run a tool that needs a human decision. The session is blocked
  until an `approval_resolved` with the same `request_id` arrives. The renderer
  shows Approve / Deny, and Approve-with-edits (send back `updated_input`).

- **`approval_resolved`** `{ request_id: string, tool_use_id: string, decision: "allow"|"deny", updated_input: object|null, reason: string|null }`
  Emitted after the decision is made, from any client. The renderer replaces the
  pending approval card with its resolved state.

- **`usage`** `{ is_error: boolean, total_cost_usd: number|null, tokens: object|null, result_text: string|null }`
  End of a turn. Maps from the SDK `ResultMessage`. Feeds the cost/token footer
  (ISSUE-011).

- **`rate_limit`** `{ status: string, rate_limit_type: string|null, resets_at: number|null, utilization: number|null, overage_status: string|null, overage_resets_at: number|null, overage_disabled_reason: string|null }`
  Rate-limit status the CLI pushes at the start of a turn, free and unasked.
  `status` is `allowed`, `allowed_warning` or `rejected`; `rate_limit_type` names
  the window (`five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`,
  `overage`); `resets_at` is Unix epoch seconds.

  `utilization` is the fraction consumed, and it is the one field to treat as
  optional in practice: an `allowed` event on a quiet account arrives without it.
  So this event reliably answers *which window and when it turns over*, and only
  sometimes *how much is gone* -- for that, see `plan_usage`.

  Render as a banner when `status` is not `allowed`, and as the footer's fallback
  reading when no `plan_usage` has been captured.

**`parent_tool_use_id`** (optional, on `text`, `thinking`, `tool_use` and
`tool_result`). Present when the block came from a **subagent** rather than the
main thread, and equal to the id of the `Agent` tool call that spawned it
(ISSUE-017).

The SDK forwards a subagent's `tool_use` and `tool_result` blocks whether or not
`forward_subagent_text` is set, so a client that ignores this field does not
merely miss the subagent's work: it draws that work at the top level, where it
reads as the main agent's own. Render these inside the matching `Agent` card.
Approvals are the deliberate exception; they stay at the top level wherever they
came from, because an approval folded inside a collapsed card is one nobody
answers.

- **`plan_usage`** `{ windows: [{key, label, percent, resets}], captured_at: number, note: string }`
  How much of the subscription's limit windows is gone: a five-hour `session`
  window, a `week` across all models, and a `week:<model>` per model. Percentages
  are what the CLI reported, and `resets` is its own wording, timezone included.

  Not polled. Asking the CLI costs a turn against the very limit being reported,
  so this event is emitted only when the **user** runs `/usage` in a session and
  the backend reads the numbers out of the answer on its way past. That makes it
  a reading with a timestamp, not a live gauge; `captured_at` and `note` (the
  CLI's own caveat that the figures cover this machine only) both belong wherever
  the numbers are shown. Render in the session footer, not as a transcript entry:
  the `/usage` output it was read from is already in the transcript above it.

- **`status`** `{ status: SessionStatus }`
  A session-level status change (see below). Cheap to render (updates the dot and
  header), and it is what replaces the old output-timing heuristic.

## SessionStatus

Derived from events, not from output timing:

`initializing` · `working` · `awaiting_approval` · `awaiting_input` · `done` · `error`

- `working`: a turn is in flight (text/tool activity).
- `awaiting_approval`: at least one `approval_request` is pending.
- `awaiting_input`: the turn finished (`usage` seen) and the session is idle,
  waiting for the next user message.
- `done`: the SDK session ended.
- `error`: a `system`/error or a failed turn.

## WebSocket protocol

Endpoint: `GET /api/ws`. The token is required as `?token=...`, since the browser
WebSocket API cannot set headers, and the handshake is refused with 401 without
it. Cross-origin requests are refused with 403. See `remote-access.md`.
Messages are JSON, one per frame.

### Server to client

- `{ "t": "sessions", "sessions": [SessionMeta, ...] }`
  The full session list. Sent on connect and whenever it changes.

- `{ "t": "snapshot", "session_id": id, "session": SessionMeta, "status": SessionStatus, "events": [TranscriptEvent, ...] }`
  Sent when a client subscribes to a session. `events` is the whole backlog so a
  late joiner (a phone opening mid-session) sees history immediately. This is why
  resume can seed from history (ISSUE-009) through the same path.

- `{ "t": "event", "event": TranscriptEvent }`
  One new event for a subscribed session.

- `{ "t": "session_update", "session": SessionMeta }`
  Session metadata or status changed.

- `{ "t": "inbox", "approvals": [{ session_id, request_id, tool_use_id, name, input, session: SessionMeta }, ...] }`
  Reply to `subscribe_inbox`: everything waiting on a decision right now, across
  every session, including approvals raised before this client connected.

- `{ "t": "inbox_event", "event": TranscriptEvent, "session": SessionMeta }`
  An `approval_request` or `approval_resolved` from any session, sent to inbox
  subscribers whatever they have open (ISSUE-010). A client subscribed to that
  session receives both this and the ordinary `event`, so that it can update its
  transcript from one and its inbox from the other without either having to guess
  which was meant. Do not count an approval twice.

- `{ "t": "error", "error": code, ... }`
  A reply to something the client asked for. Codes: `stale_or_unknown_request`
  (a `resolve` naming an approval that is no longer pending), `session_start_failed`
  (a `create` that could not start, with `detail`), `unknown_permission_mode`,
  `unknown_session`, and `request_failed` for anything else. None of these close
  the socket.

### Subagent transcripts

`GET /api/sessions/{id}/subagents/{agent_id}` returns
`{ "events": [TranscriptEvent, ...] }` for one subagent of a replayed session,
where `agent_id` came from a `tool_result`'s `agent_id`.

Fetched rather than seeded because a subagent's transcript routinely runs larger
than the parent it belongs to; seeding every one would bury the conversation and
re-send on every reconnect. An unknown or malformed id returns an empty list
rather than an error: a subagent that was launched and recorded nothing is a
real state, and the id reaches this route from a file on disk.

### Commands and skills

`GET /api/sessions/{id}/commands` returns `{ "commands": [{name, description,
argumentHint, aliases?}] }` for a live session (ISSUE-016).

One list, because the CLI returns one: a skill sits beside `/compact` and
`/model` and is invoked the same way. The backend asks the CLI once at connect
via the SDK's `get_server_info()` and caches the answer, which does not change
for a session's lifetime. A session with no runtime, or one whose CLI did not
answer, returns an empty list rather than an error, because "this session has
none" is a real state.

### Stored transcripts, without a session

Three routes serve conversations the CLI recorded, which belong to no live
session and may never get one (ISSUE-015).

`GET /api/history/search?q=…` returns
`{ "query": …, "hits": [{ session_id, cwd, title, mtime, match_count, snippets }] }`,
newest first. `snippets` are `{role, text}` excerpts around the match, labelled
for what the content is rather than the transcript's own role field: the CLI
records a tool result as a `user` message, and labelling it that way tells a
reader nothing. An optional `under=<path>` restricts the search to one tree.

It is a scan, not an index. 65 transcripts and 131 MiB answer in 155-485 ms,
because a raw substring test over each file's bytes rejects most of them without
parsing, and JSON is parsed only for lines that matched. See `history_search.py`
for when that stops being true.

`GET /api/history/{sdk_session_id}` returns `{ session_id, cwd, title, mtime,
events }` for reading a conversation without resuming it. `index.html?history=…`
renders it through the ordinary transcript renderer with the composer closed, so
a stored transcript and a running one cannot drift apart in how they look.

`POST /api/history/{sdk_session_id}/resume` starts a live session from one. It
is separate from the project resume route rather than sharing it: that route
refuses any id its project does not own, which is right when a project id is in
the URL, and wrong here, where a search hit can live in a folder that was never
registered as a project. An already-open conversation is handed back rather than
started twice.

### Client to server

- `{ "t": "subscribe", "session_id": id }` / `{ "t": "unsubscribe", "session_id": id }`
- `{ "t": "send", "session_id": id, "text": string }`
  A new user message. Backend calls the SDK client's `query`.
- `{ "t": "interrupt", "session_id": id }`
  Stops the turn a session is in the middle of (ISSUE-033). The turn ends at a
  safe point rather than instantly. There is no reply either way: by the time a
  stop reaches the server the turn may already have finished, and telling the
  user their stop failed would be worse than saying nothing.
- `{ "t": "resolve", "session_id": id, "request_id": id, "decision": "allow"|"deny", "updated_input": object|null, "reason": string|null }`
  Resolves a pending `approval_request`. **`session_id` is required**: approvals
  are held per session, so without it the server has nothing to look up and
  answers `stale_or_unknown_request`. This line previously omitted it, and the
  browser client omitted it to match, which is how approving from the pane came
  to do nothing at all. First valid resolution wins; later ones for the same
  `request_id` get the error reply (ISSUE-008).
- `{ "t": "create", "name": string, "workdir": string, "permission_mode": string, "resume": sdk_session_id|null, "text": string|null }`
  Creates an `sdk` session. `resume` seeds the transcript from that session's
  stored history (ISSUE-009); `text` is an optional first message. The desktop
  dialog can also create sessions directly.
- `{ "t": "close", "session_id": id }`
  Ends a session: the runtime disconnects its `claude` subprocess, then the
  manager drops it and broadcasts the shorter `sessions` list. A session the
  server does not have comes back as `close_failed`. Whether to ask the user
  first is the client's business; the browser client confirms in the row for
  any session that has not already finished, and the desktop confirms in a
  dialog. The CLI's own transcript is untouched, so the conversation can be
  resumed afterwards.
- `{ "t": "rename", "session_id": id, "name": string }`
  Names a session by hand. The name sticks: a session renamed this way is no
  longer renamed by the generated title below. An empty name, or one for a
  session the server does not have, comes back as `rename_failed`.
- `{ "t": "subscribe_inbox" }` / `{ "t": "unsubscribe_inbox" }`
  Start or stop receiving `inbox` and `inbox_event` (ISSUE-010).
- `{ "t": "set_permission_mode", "session_id": id, "mode": "default"|"acceptEdits"|"plan"|"bypassPermissions"|"dontAsk"|"auto" }`
  Changes the mode of a live session (ISSUE-012). It applies from the next tool
  call onward, not to one already in flight. The change comes back as a `system`
  event with subtype `permission_mode`.

### SessionMeta

`{ id, name, name_is_custom, workdir, kind, status, created_at,
sdk_session_id, permission_mode, cost_usd, tokens, totals_cover_this_run_only,
project_id }`

There is no `goal`. Sessions carried one until it was removed: it was set at
the new-session dialog, shown on the row, and never sent to the model, so it
described the session to nobody but the person who typed it. `workdir` is what
the row's second line carries now.

A session opens named after the folder it runs in, which is already beside it
on the row, so several in one project read as the same session. It adopts the
title the CLI generates for the conversation instead -- read back out of the
transcript at the end of each turn, and at once for a resumed one, since the
CLI refines the title as the conversation goes on. `name_is_custom` is true
once someone has renamed the session, which stops that adoption for good.

`cost_usd` is cumulative for the conversation, as the SDK reports it, so it is
assigned rather than accumulated. `tokens` is
`{ input, output, cache_read, cache_creation }` and **is** accumulated, because
those counts arrive per turn. `totals_cover_this_run_only` is true for a resumed
session: seeded history carries no usage events, so the totals cover only what
this run has spent (ISSUE-011).

`project_id` is the registered project whose folder contains the session's
`workdir`, or null when it is not inside one. It is derived on read rather than
stored, so registering a folder immediately claims the sessions already running
inside it (`project-workspace-protocol.md`).

## Rendering notes for the shared client

- Group consecutive `text`/`thinking` from the same role into one bubble.
- Pair `tool_use` with its `tool_result` by `tool_use_id` into one card; show the
  tool name, a pretty-printed input, and a collapsible result. `Edit` renders as a
  diff, `Write` as file content, and `ExitPlanMode` as a formatted plan, in the
  approval card as well as the tool card (ISSUE-013).
- Head each tool card with one line naming what the call is doing -- the command
  for `Bash`, the path for `Read`/`Edit`/`Write`, the pattern for `Grep`. A
  transcript is mostly tool cards, so that line carries the narrative and the
  argument JSON below it is reference (ISSUE-026). Show short results inline;
  fold long ones behind a toggle that says how many lines are behind it.
- An `approval_request` with no matching `approval_resolved` is a live, actionable
  card. Once resolved, collapse it to a one-line outcome. While such a card is
  scrolled out of view, offer a way back to it: an approval the user cannot see
  blocks the session silently (ISSUE-025).
- Entries in the transcript keep their natural height. The transcript is a
  scrolling flex column, so any child that hides its overflow must also set
  `flex-shrink: 0` or the column crushes it instead of scrolling (ISSUE-025).
- Nest a `tool_use`/`tool_result`/`text`/`thinking` carrying
  `parent_tool_use_id` inside the `Agent` card whose id it names, collapsed by
  default: a subagent routinely produces more events than the parent it runs
  under. Key tool cards by `tool_use_id` globally rather than per container, so
  a result pairs with its call wherever either rendered.
- The composer stays usable while a turn is running. Messages queue, in Codinian
  and again in the CLI, and land when the turn ends (ISSUE-028).
- Theme-aware (light and dark), responsive, no external network dependencies:
  the same bundle loads inside WebKitGTK and in a plain browser.
- `?theme=light` or `?theme=dark` on the page URL forces a palette; with no such
  parameter the page follows `prefers-color-scheme`. The value is stamped onto
  `<html data-theme>` by a script in `<head>`, before first paint, so the page
  never shows the wrong palette and then corrects itself. `styles.css` defines
  the light palette on bare `:root` and the dark one twice, under the media
  query and under `[data-theme="dark"]`, with the media query guarded by
  `:not([data-theme="light"])` so an explicit light choice wins on a dark
  desktop. The desktop app appends the parameter from the user's setting and
  reloads its panes when it changes, since the attribute is read only at load
  (ISSUE-030).
