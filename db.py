import sqlite3
from datetime import datetime
from pathlib import Path

from session import Session, Status

DB_PATH = Path.home() / ".local/share/codinian/codinian.db"


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
    if "resume_session_id" not in columns:
        con.execute("ALTER TABLE sessions ADD COLUMN resume_session_id TEXT")
    con.commit()
    return con


def save_session(con: sqlite3.Connection, session: Session) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO sessions
            (id, name, workdir, status, created_at, last_output_at, pid, resume_session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    con.commit()


def load_sessions(con: sqlite3.Connection) -> list[Session]:
    # Named columns rather than `SELECT *`: a database written before the goal
    # column was dropped still has it, and positional reads would take the goal
    # for the working directory on every one of those rows. The stale column is
    # left where it is, since it has a default and nothing writes it.
    rows = con.execute(
        """
        SELECT id, name, workdir, status, created_at, last_output_at, pid,
               resume_session_id
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
        )
        for row in rows
    ]


def delete_session(con: sqlite3.Connection, session_id: str) -> None:
    con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    con.commit()
