"""Session metadata in SQLite. The transcript itself is not here: the durable
record of a conversation is the CLI's own JSONL under ~/.claude/projects, and
this table holds only what the sidebar needs to describe a session.

Sessions live in memory for the life of the process and are not restored at
startup, so nothing currently reads a row back into a `SessionManager`;
`load_sessions` is used for session ids alone. The columns are kept faithful to
the dataclass anyway, so that if a restore is ever wired up it gets a session
back as it was rather than as a terminal session with no totals (ISSUE-040).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from session import Session, Status

DB_PATH = Path.home() / ".local/share/codinian/codinian.db"

# Columns added after the table's first shape. Each is applied with ALTER TABLE
# on a database that predates it, which is the whole migration story here: the
# table is metadata, every column is nullable or defaulted, and a row written by
# an older version reads back as a session with those fields at their defaults.
_ADDED_COLUMNS = {
    "resume_session_id": "TEXT",
    "kind": "TEXT",
    "name_is_custom": "INTEGER",
    "permission_mode": "TEXT",
    "sdk_session_id": "TEXT",
    "cost_usd": "REAL",
    "tokens": "TEXT",              # the four counts, as JSON
    "totals_cover_this_run_only": "INTEGER",
}

_EMPTY_TOKENS = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}


def open_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            workdir     TEXT NOT NULL,
            status      TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            last_output_at TEXT,
            pid         INTEGER
        )
    """)
    columns = {row[1] for row in con.execute("PRAGMA table_info(sessions)")}
    for name, decl in _ADDED_COLUMNS.items():
        if name not in columns:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {name} {decl}")
    con.commit()
    return con


def save_session(con: sqlite3.Connection, session: Session) -> None:
    """Write the whole session, SDK fields included.

    `sdk_status` is the one field deliberately left out. It is derived from the
    events of a running turn loop, and no turn loop survives the process, so a
    stored one would only ever be a stale claim that a session was working.
    """
    con.execute(
        """
        INSERT OR REPLACE INTO sessions
            (id, name, workdir, status, created_at, last_output_at, pid,
             resume_session_id, kind, name_is_custom, permission_mode,
             sdk_session_id, cost_usd, tokens, totals_cover_this_run_only)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session.id,
            session.name,
            session.workdir,
            session.status.value,
            session.created_at.isoformat(),
            session.last_output_at.isoformat() if session.last_output_at else None,
            session.pid,
            session.resume_session_id,
            session.kind,
            int(session.name_is_custom),
            session.permission_mode,
            session.sdk_session_id,
            session.cost_usd,
            json.dumps(session.tokens),
            int(session.totals_cover_this_run_only),
        ),
    )
    con.commit()


def _tokens_from_row(raw) -> dict:
    """The stored token counts, defaulted for a row written before the column
    existed or left holding something unreadable."""
    tokens = dict(_EMPTY_TOKENS)
    if not raw:
        return tokens
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        return tokens
    if isinstance(stored, dict):
        for key in tokens:
            value = stored.get(key)
            if isinstance(value, (int, float)):
                tokens[key] = value
    return tokens


def load_sessions(con: sqlite3.Connection) -> list[Session]:
    # Named columns rather than `SELECT *`: a database written before the goal
    # column was dropped still has it, and positional reads would take the goal
    # for the working directory on every one of those rows. The stale column is
    # left where it is, since it has a default and nothing writes it.
    rows = con.execute(
        """
        SELECT id, name, workdir, status, created_at, last_output_at, pid,
               resume_session_id, kind, name_is_custom, permission_mode,
               sdk_session_id, cost_usd, tokens, totals_cover_this_run_only
        FROM sessions ORDER BY created_at
        """
    ).fetchall()
    return [
        Session(
            id=row[0],
            name=row[1],
            workdir=row[2],
            status=Status(row[3]),
            created_at=datetime.fromisoformat(row[4]),
            last_output_at=datetime.fromisoformat(row[5]) if row[5] else None,
            pid=row[6],
            resume_session_id=row[7],
            kind=row[8] or "terminal",
            name_is_custom=bool(row[9]),
            permission_mode=row[10] or "default",
            sdk_session_id=row[11],
            cost_usd=row[12] or 0.0,
            tokens=_tokens_from_row(row[13]),
            totals_cover_this_run_only=bool(row[14]),
        )
        for row in rows
    ]


def delete_session(con: sqlite3.Connection, session_id: str) -> None:
    con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    con.commit()
