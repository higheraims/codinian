"""Session metadata in SQLite, including the ALTER TABLE path an older
database takes on first open."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from codinian import db
from codinian.events import SessionStatus
from codinian.session import Session, Status


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "codinian.db")
    connection = db.open_db()
    yield connection
    connection.close()


def make_session(**overrides) -> Session:
    fields = dict(
        id="abc12345", name="A session", workdir="/tmp/proj",
        status=Status.RUNNING, created_at=datetime(2026, 8, 24, 10, 30),
        kind="sdk", permission_mode="acceptEdits", sdk_session_id="uuid-1",
        cost_usd=0.42, totals_cover_this_run_only=True,
        tokens={"input": 10, "output": 20, "cache_read": 30, "cache_creation": 40},
    )
    fields.update(overrides)
    return Session(**fields)


def test_open_db_creates_the_parent_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "nested" / "deeper" / "codinian.db")
    db.open_db().close()
    assert (tmp_path / "nested" / "deeper" / "codinian.db").is_file()


def test_a_session_round_trips_with_every_sdk_field(con):
    original = make_session()
    db.save_session(con, original)

    loaded = db.load_sessions(con)
    assert len(loaded) == 1
    restored = loaded[0]
    for field in ("id", "name", "workdir", "status", "created_at", "kind",
                  "permission_mode", "sdk_session_id", "cost_usd", "tokens",
                  "totals_cover_this_run_only", "name_is_custom"):
        assert getattr(restored, field) == getattr(original, field), field


def test_sdk_status_is_deliberately_not_stored(con):
    saved = make_session()
    saved.sdk_status = SessionStatus.WORKING
    db.save_session(con, saved)
    # No turn loop survives the process, so a stored status would only ever be
    # a stale claim that a session was working.
    assert db.load_sessions(con)[0].sdk_status is None


def test_saving_the_same_id_twice_replaces_rather_than_duplicates(con):
    db.save_session(con, make_session(name="First"))
    db.save_session(con, make_session(name="Second"))
    assert [s.name for s in db.load_sessions(con)] == ["Second"]


def test_sessions_come_back_in_creation_order(con):
    db.save_session(con, make_session(id="later", created_at=datetime(2026, 8, 25)))
    db.save_session(con, make_session(id="earlier", created_at=datetime(2026, 8, 24)))
    assert [s.id for s in db.load_sessions(con)] == ["earlier", "later"]


def test_delete_removes_one_session_and_leaves_the_others(con):
    db.save_session(con, make_session(id="keep-me"))
    db.save_session(con, make_session(id="drop-me"))
    db.delete_session(con, "drop-me")
    assert [s.id for s in db.load_sessions(con)] == ["keep-me"]


def test_deleting_an_unknown_id_is_not_an_error(con):
    db.delete_session(con, "never-existed")


def test_last_output_at_survives_as_none(con):
    db.save_session(con, make_session(last_output_at=None))
    assert db.load_sessions(con)[0].last_output_at is None


def test_last_output_at_survives_as_a_datetime(con):
    when = datetime(2026, 8, 24, 11, 45, 30)
    db.save_session(con, make_session(last_output_at=when))
    assert db.load_sessions(con)[0].last_output_at == when


# ------------------------------------------------------------- migration

def test_a_database_from_before_the_sdk_columns_is_migrated_on_open(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, workdir TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL,
            last_output_at TEXT, pid INTEGER
        )
    """)
    old.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("old-row", "From an older version", "/tmp/x", "idle",
         datetime(2026, 1, 1).isoformat(), None, None),
    )
    old.commit()
    old.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    con = db.open_db()

    restored = db.load_sessions(con)[0]
    assert restored.id == "old-row"
    assert restored.kind == "terminal"
    assert restored.permission_mode == "default"
    assert restored.cost_usd == 0.0
    assert restored.tokens == {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    assert restored.totals_cover_this_run_only is False
    con.close()


def test_opening_an_already_migrated_database_twice_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "codinian.db")
    first = db.open_db()
    db.save_session(first, make_session())
    first.close()

    second = db.open_db()
    assert len(db.load_sessions(second)) == 1
    second.close()


def test_a_stale_column_does_not_shift_the_columns_that_are_read(con):
    # A database written before `goal` was dropped still carries it. Reading
    # positionally would take the goal for the working directory.
    con.execute("ALTER TABLE sessions ADD COLUMN goal TEXT DEFAULT 'a stale goal'")
    db.save_session(con, make_session(workdir="/tmp/the-real-workdir"))
    assert db.load_sessions(con)[0].workdir == "/tmp/the-real-workdir"


@pytest.mark.parametrize("stored, expected_input", [
    (None, 0),
    ("", 0),
    ("{not json", 0),
    ('["a list"]', 0),
    ('{"input": "not a number"}', 0),
    ('{"input": 5}', 5),
])
def test_unreadable_token_counts_default_to_zero(con, stored, expected_input):
    db.save_session(con, make_session())
    con.execute("UPDATE sessions SET tokens = ?", (stored,))
    con.commit()
    tokens = db.load_sessions(con)[0].tokens
    assert tokens["input"] == expected_input
    assert set(tokens) == {"input", "output", "cache_read", "cache_creation"}


def test_token_counts_are_stored_as_json(con):
    db.save_session(con, make_session())
    raw = con.execute("SELECT tokens FROM sessions").fetchone()[0]
    assert json.loads(raw)["cache_creation"] == 40
