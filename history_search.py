"""Full-text search across every stored transcript (ISSUE-015).

The resume picker shows a session's first message and how long ago it was
touched, which is enough to recognise a conversation you remember and useless
for finding one you do not. This searches the text of every conversation the
CLI has recorded, so "the session where that traceback appeared" is findable
without resuming anything.

## Why there is no index

ISSUE-015's worklog assumed "thousands of JSONL files" and an index, probably in
the existing SQLite database. Measured instead: 65 transcripts, 131 MiB, and a
full scan answers a query in 155 to 485 ms depending on how many files contain
the needle. That is inside the range where a search box feels immediate, and an
index would add a build step, a staleness question, and a second copy of the
user's conversations on disk for no gain.

The scan is only fast because it does not parse. A raw substring test over the
file's bytes rejects most files outright, and JSON is parsed only for the lines
that actually matched. Parsing 131 MiB of JSON per query would not be viable.

Revisit this when a scan stops answering inside about a second. At the current
ratio that is somewhere north of 400 MiB, and the natural next step is a small
inverted index keyed by session id rather than putting transcript text in the
session database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import claude_history

# How many matching lines to keep per transcript. A query like "def " matches
# thousands of times in one file; nobody reads past the first few, and carrying
# them all would make the response larger than the answer.
MAX_SNIPPETS_PER_SESSION = 5

# How many transcripts to report. Ordered by recency, so the cut falls on the
# oldest rather than on whatever the filesystem happened to list last.
MAX_SESSIONS = 50

# Characters of context either side of the needle inside a snippet.
CONTEXT = 90


@dataclass
class Snippet:
    """One matching line, rendered as something a person can read."""

    role: str
    text: str

    def to_dict(self) -> dict:
        return {"role": self.role, "text": self.text}


@dataclass
class SearchHit:
    session_id: str
    cwd: str
    title: str
    mtime: datetime
    match_count: int
    snippets: list[Snippet] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "title": self.title,
            "mtime": self.mtime.isoformat(),
            "match_count": self.match_count,
            "snippets": [s.to_dict() for s in self.snippets],
        }


def _transcripts(under: str | Path | None = None) -> list[Path]:
    """Every stored main transcript, newest first.

    Subagent transcripts are deliberately excluded. They live one directory
    deeper and their text is the parent's work seen from inside; including them
    would report the same conversation twice under two different ids, and the
    second id is not one the resume path can open.
    """
    if not claude_history.PROJECTS_DIR.is_dir():
        return []
    paths = list(claude_history.PROJECTS_DIR.glob("*/*.jsonl"))
    if under is not None:
        predicate = claude_history._under(under)
        paths = [p for p in paths if _cwd_of(p) and predicate(_cwd_of(p))]
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def _cwd_of(path: Path) -> str | None:
    """The working directory a transcript records, read from its first usable
    line rather than by parsing the whole file."""
    try:
        with path.open() as handle:
            for _ in range(40):
                line = handle.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("cwd"):
                    return entry["cwd"]
    except OSError:
        return None
    return None


def _readable_blocks(entry: dict) -> list[tuple[str, str]]:
    """A transcript line as a list of (label, text), one per content block.

    Per block rather than per line so a snippet can come from whichever block
    actually matched, and be labelled for what it is. The CLI records a tool
    result as a `user` message, so labelling by the line's role would put
    "user" next to a wall of command output and tell the reader nothing.
    """
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    role = entry.get("type") or message.get("role") or ""
    content = message.get("content")

    if isinstance(content, str):
        return [(role, content)] if content.strip() else []
    if not isinstance(content, list):
        return []

    out: list[tuple[str, str]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.append((role, block.get("text", "")))
        elif kind == "thinking":
            out.append(("thinking", block.get("thinking", "")))
        elif kind == "tool_use":
            # The tool's name leads, because that is what identifies the moment.
            out.append((f"{block.get('name') or 'tool'} call",
                        json.dumps(block.get("input", {}))))
        elif kind == "tool_result":
            body = block.get("content")
            if isinstance(body, str):
                out.append(("tool result", body))
            elif isinstance(body, list):
                for b in body:
                    if isinstance(b, dict) and b.get("type") == "text":
                        out.append(("tool result", b.get("text", "")))
    return [(label, text) for label, text in out if text.strip()]


def _excerpt(text: str, needle_lower: str) -> str:
    """The region of `text` around the first match, on one line."""
    flat = " ".join(text.split())
    at = flat.lower().find(needle_lower)
    if at < 0:
        return flat[: CONTEXT * 2] + ("…" if len(flat) > CONTEXT * 2 else "")
    start = max(0, at - CONTEXT)
    end = min(len(flat), at + len(needle_lower) + CONTEXT)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def search(query: str, under: str | Path | None = None,
           limit: int = MAX_SESSIONS) -> list[SearchHit]:
    """Transcripts containing `query`, newest first.

    Case-insensitive substring, not regex: this backs a search box, and a
    mistyped pattern should return nothing rather than an error. `under`
    restricts the search to one project tree.
    """
    needle = query.strip()
    if not needle:
        return []
    lower = needle.lower()
    raw = lower.encode("utf-8", "replace")

    hits: list[SearchHit] = []
    for path in _transcripts(under):
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        # The cheap rejection that makes a scan viable: most files do not
        # contain the needle, and this settles that without parsing anything.
        folded = blob.lower()
        count = folded.count(raw)
        if not count:
            continue

        snippets: list[Snippet] = []
        try:
            with path.open() as handle:
                for line in handle:
                    if len(snippets) >= MAX_SNIPPETS_PER_SESSION:
                        break
                    if lower not in line.lower():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    for label, text in _readable_blocks(entry):
                        if lower not in text.lower():
                            # Either this block is not the one that matched, or
                            # the match was in the line's metadata (a uuid, a
                            # timestamp) rather than in anything anyone wrote.
                            continue
                        snippets.append(Snippet(label, _excerpt(text, lower)))
                        if len(snippets) >= MAX_SNIPPETS_PER_SESSION:
                            break
        except OSError:
            continue

        if not snippets:
            continue

        session = claude_history._read_session(path)
        hits.append(SearchHit(
            session_id=path.stem,
            cwd=(session.cwd if session else _cwd_of(path)) or "",
            title=(session.title if session else "") or "(untitled)",
            mtime=datetime.fromtimestamp(path.stat().st_mtime),
            match_count=count,
            snippets=snippets,
        ))
        if len(hits) >= limit:
            break
    return hits
