"""Reads the `claude` CLI's own JSONL transcripts under ~/.claude/projects.

Two jobs. `list_prior_sessions` powers the resume picker, and, filtered by
`under`, the history a project's Sessions tab shows (ISSUE-024).
`events_for_session` maps a stored transcript to the TranscriptEvents in
events.py (ISSUE-009), so a resumed session opens showing what was already said
instead of an empty pane. The SDK's `resume` continues the conversation but does
not replay it, and the model remembering something the user cannot see is the
wrong way round.

The mapping deliberately lands on the same event shapes the live SDK driver
produces, so seeded history and live turns render identically.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Line types the CLI writes for its own bookkeeping, carrying nothing a reader
# of the conversation wants. Anything not listed here and not understood is
# skipped too, so an unfamiliar line never costs us the surrounding turns.
_BOOKKEEPING_TYPES = frozenset({
    "attachment", "mode", "permission-mode", "bridge-session", "ai-title",
    "last-prompt", "queue-operation", "file-history-snapshot",
})

# A very long conversation would otherwise seed tens of thousands of events into
# a pane nobody scrolls that far back in. Keep the most recent ones and say so.
MAX_SEEDED_EVENTS = 2000

# What an agent id is allowed to look like before it is put in a path. The ids
# the CLI writes are hex, but this value reaches here from a stored file and
# then from a client, so it is validated rather than assumed.
_SAFE_AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass
class PriorSession:
    id: str
    cwd: str
    title: str
    mtime: datetime


def _extract_title(entry: dict) -> str | None:
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                break
    else:
        text = ""
    if not text or text.startswith("<"):
        return None
    return text.splitlines()[0][:120]


def _read_session(path: Path) -> PriorSession | None:
    session_id = path.stem
    cwd = None
    title = None
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "user":
                    continue
                if cwd is None and entry.get("cwd"):
                    cwd = entry["cwd"]
                if title is None:
                    title = _extract_title(entry)
                if cwd and title:
                    break
    except OSError:
        return None

    if not cwd:
        return None
    return PriorSession(
        id=session_id,
        cwd=cwd,
        title=title or "(no messages)",
        mtime=datetime.fromtimestamp(path.stat().st_mtime),
    )


def last_permission_mode(sdk_session_id: str) -> str | None:
    """The permission mode a stored conversation was last running under.

    The CLI records `permissionMode` on its own user records, so resuming can
    put the session back in the mode it left rather than in whatever the
    project's default happens to be -- which is how a conversation that had been
    running unattended came back asking to approve every command.

    The last one wins, not the first: the mode can be changed mid-conversation,
    and it is the state at the end that resuming continues from. Returns None
    when the transcript records none, leaving the caller's own defaults to
    decide.
    """
    path = transcript_path(sdk_session_id)
    if path is None:
        return None
    found = None
    try:
        with path.open() as f:
            for line in f:
                # A cheap reject before parsing. Transcript lines routinely run
                # to tens of thousands of characters -- an image result is the
                # whole picture in base64 -- and parsing every one of them to
                # find a short string costs far more than looking for it.
                if '"permissionMode"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mode = entry.get("permissionMode")
                if isinstance(mode, str) and mode:
                    found = mode
    except OSError:
        return None
    return found


# How long a name taken from a transcript is allowed to be. The CLI's own
# titles run to a handful of words; the fallback is the first line of the
# opening message, which can be a paragraph.
_NAME_MAX = 60


def _last_ai_title(path: Path) -> str | None:
    """The most recent title the CLI generated for this transcript.

    It writes an `ai-title` line once it has enough of the conversation to name
    it, and writes a fresh one as the conversation moves on, so the last is the
    current answer rather than the first.
    """
    found = None
    try:
        with path.open() as f:
            for line in f:
                # The same cheap reject as last_permission_mode, for the same
                # reason: a transcript line can be a whole base64 image, and
                # parsing every one of them to find a short string costs far
                # more than looking for it.
                if '"ai-title"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                title = entry.get("aiTitle")
                if isinstance(title, str) and title.strip():
                    found = title.strip()
    except OSError:
        return None
    return found


def generated_name(sdk_session_id: str) -> str | None:
    """A name for a live conversation, from the CLI's own record of it.

    Sessions arrive named after the folder they run in, which is the one thing
    about them already on screen, so a second session in the same project is
    indistinguishable from the first. The CLI names its own sessions and files
    the name in the transcript; this reads it back.

    Falls back to the first line of the opening message, which is what the
    resume picker shows, for the window before the first title lands and for a
    CLI old enough not to write one. Returns None when the transcript has
    neither -- every session up to its first message.
    """
    path = transcript_path(sdk_session_id)
    if path is None:
        return None
    name = _last_ai_title(path)
    if name is None:
        prior = _read_session(path)
        if prior is not None and prior.title != "(no messages)":
            name = prior.title
    if not name:
        return None
    return name if len(name) <= _NAME_MAX else name[:_NAME_MAX].rstrip() + "\u2026"


def _under(root: str | Path):
    """A predicate for "this working directory is inside `root`".

    Compares resolved path *components*, not strings, so a project at
    /home/x/proj never claims a session run in /home/x/proj-other. The CLI
    names its per-project directories after a mangled form of the working
    directory, which would be a cheaper filter, but the mangling is lossy
    (every separator becomes a dash), so the recorded `cwd` is the only
    answer worth trusting.
    """
    root_parts = Path(root).resolve().parts

    def predicate(cwd: str) -> bool:
        try:
            parts = Path(cwd).resolve().parts
        except OSError:
            return False
        return parts[:len(root_parts)] == root_parts

    return predicate


def list_prior_sessions(limit: int = 200,
                        under: str | Path | None = None) -> list[PriorSession]:
    """Prior CLI sessions, newest first.

    `under` restricts the result to sessions whose recorded working directory
    is inside that folder; the Sessions tab passes a project root. A session run
    in a subdirectory of the project counts as being in it. The CLI keeps one
    directory per working directory rather than one per repo, so the scan reads
    every transcript either way and filters on what each one recorded.
    """
    if not PROJECTS_DIR.is_dir():
        return []
    inside = _under(under) if under is not None else None

    sessions = []
    for jsonl_path in PROJECTS_DIR.glob("*/*.jsonl"):
        session = _read_session(jsonl_path)
        if session is None:
            continue
        if inside is not None and not inside(session.cwd):
            continue
        sessions.append(session)

    sessions.sort(key=lambda s: s.mtime, reverse=True)
    return sessions[:limit]


# --------------------------------------------------------------- transcript

def transcript_path(sdk_session_id: str) -> Path | None:
    """The JSONL file for an SDK session id. The CLI files them under a
    directory named for the project, so this searches rather than guessing."""
    if not sdk_session_id or not PROJECTS_DIR.is_dir():
        return None
    for path in PROJECTS_DIR.glob(f"*/{sdk_session_id}.jsonl"):
        return path
    return None


def subagent_dir(sdk_session_id: str) -> Path | None:
    """Where a session's subagent transcripts live, if it has any.

    The CLI files them one per subagent in a directory named for the session,
    beside the session's own JSONL:

        ~/.claude/projects/<project>/<session-uuid>/subagents/agent-<id>.jsonl

    Note the main transcript contains no sidechain entries at all: a subagent's
    work is not interleaved and skipped, it was never in that file (ISSUE-017).
    """
    path = transcript_path(sdk_session_id)
    if path is None:
        return None
    directory = path.with_suffix("") / "subagents"
    return directory if directory.is_dir() else None


def subagent_path(sdk_session_id: str, agent_id: str) -> Path | None:
    """One subagent's transcript. `agent_id` comes off the parent's tool_result
    and is checked rather than trusted: it lands in a filename, and the id in a
    stored transcript is not something this module gets to assume is clean."""
    directory = subagent_dir(sdk_session_id)
    if directory is None or not agent_id or not _SAFE_AGENT_ID.fullmatch(agent_id):
        return None
    path = directory / f"agent-{agent_id}.jsonl"
    return path if path.is_file() else None


def _blocks(entry: dict) -> list:
    content = entry.get("message", {}).get("content")
    if isinstance(content, list):
        return content
    if isinstance(content, str) and content.strip():
        return [{"type": "text", "text": content}]
    return []


def _assistant_events(entry: dict):
    for block in _blocks(entry):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text", "")
            if text.strip():
                yield "text", {"role": "assistant", "text": text}
        elif kind == "thinking":
            thinking = block.get("thinking", "")
            if thinking.strip():
                yield "thinking", {"text": thinking}
        elif kind == "tool_use":
            yield "tool_use", {
                "tool_use_id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input", {}),
            }


def _subagent_meta(entry: dict) -> dict:
    """What a parent `tool_result` says about the subagent it is reporting.

    The link from an `Agent` call to its transcript is `toolUseResult.agentId`,
    not `sourceToolAssistantUUID`, which does not resolve to anything in the
    parent. The same record carries what a card header wants, so it is read out
    here rather than opening the subagent's file to find out (ISSUE-017)."""
    result = entry.get("toolUseResult")
    if not isinstance(result, dict) or not result.get("agentId"):
        return {}
    meta = {"agent_id": result["agentId"]}
    for key, field in (("description", "agent_description"),
                       ("resolvedModel", "agent_model"),
                       ("status", "agent_status")):
        if result.get(key):
            meta[field] = result[key]
    return meta


def _user_events(entry: dict):
    # Turns the CLI injected itself -- skill bodies, hook output, system
    # reminders -- are marked isMeta and dropped by events_for_session before
    # they reach here, so any user text at this point is what the user typed.
    # A live session marks the injected ones `injected` instead and folds them
    # into a collapsed card (ISSUE-026); replay simply omits them.
    for block in _blocks(entry):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_result":
            yield "tool_result", {
                "tool_use_id": block.get("tool_use_id"),
                "is_error": block.get("is_error"),
                "content": block.get("content"),
                # Present only when this result closes an `Agent` call. The
                # client uses it to offer the subagent's transcript, which is
                # fetched on demand rather than seeded: those transcripts run
                # larger than the parent they belong to.
                **_subagent_meta(entry),
            }
        elif kind == "text":
            text = block.get("text", "")
            if text.strip():
                yield "text", {"role": "user", "text": text, "source": "operator"}


def _events_from(path: Path, limit: int, skip_sidechain: bool = True,
                 tag: dict | None = None) -> list[tuple[str, dict]]:
    """Parse one JSONL transcript into events, oldest first.

    Shared by the main transcript and by each subagent's, because the CLI
    writes the same message shape in both. `skip_sidechain` is the difference:
    a subagent's file is entirely sidechain entries, which is exactly what a
    reader of that file wants and exactly what a reader of the parent does not.
    """
    events: list[tuple[str, dict]] = []
    try:
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") in _BOOKKEEPING_TYPES:
                    continue
                if entry.get("isMeta"):
                    continue
                if skip_sidechain and entry.get("isSidechain"):
                    # A subagent's turns are not in this file (see
                    # subagent_dir), but an older transcript may still carry
                    # them inline; either way they do not belong in the main
                    # thread's reading order.
                    continue

                if entry.get("type") == "assistant":
                    events.extend(_assistant_events(entry))
                elif entry.get("type") == "user":
                    events.extend(_user_events(entry))
    except OSError:
        return []

    if tag:
        for _etype, data in events:
            data.update(tag)

    if len(events) > limit:
        dropped = len(events) - limit
        events = events[-limit:]
        events.insert(0, ("system", {
            "subtype": "history_truncated",
            "data": {"message": f"{dropped} earlier events are not shown. "
                                f"The model still has the full conversation."},
            **(tag or {}),
        }))
    return events


def subagent_events(sdk_session_id: str, agent_id: str,
                    limit: int = MAX_SEEDED_EVENTS) -> list[tuple[str, dict]]:
    """One subagent's transcript, as events tagged with the `Agent` call they
    belong to, ready to render inside that card.

    The parent tool_use id is not recorded in the subagent's own file, so the
    caller supplies it; everything else parses with the same two mappers the
    main transcript uses, because the CLI writes the same message shape."""
    path = subagent_path(sdk_session_id, agent_id)
    if path is None:
        return []
    events = _events_from(path, limit, skip_sidechain=False)

    # A subagent's opening user turn is the briefing the parent handed it, not
    # conversation, and it is long: rendered as a bubble it fills the whole
    # card before any of the work shows. Same treatment as an injected turn on
    # the live path (ISSUE-026), with a label that says what it is.
    for etype, data in events:
        if etype == "text" and data.get("role") == "user":
            data["source"] = "briefing"
            break
    return events


def events_for_session(sdk_session_id: str,
                       limit: int = MAX_SEEDED_EVENTS) -> list[tuple[str, dict]]:
    """A stored transcript as (event_type, data) pairs, oldest first.

    Returns an empty list when there is no transcript to read, which is the
    normal case for a session that has not run yet."""
    path = transcript_path(sdk_session_id)
    if path is None:
        return []
    return _events_from(path, limit)
