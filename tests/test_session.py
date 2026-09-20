"""Sessions and the transcript event bus.

The event bus is where a session's status, cost and token totals are actually
decided: `add_event` folds a status or usage event into the Session as it goes
past. Getting that wrong shows up as a sidebar that keeps claiming a finished
session is working, or a cost figure that resets every turn.
"""

from __future__ import annotations

import pathlib
from datetime import datetime

import pytest

from codinian import session as session_module
from codinian.events import EVENT_TYPES, SessionStatus, TranscriptEvent
from codinian.session import Session, SessionManager, Status


@pytest.fixture(autouse=True)
def no_project_resolver():
    """`set_project_resolver` installs a module global. Left behind, it leaks
    into every later test in the run."""
    session_module.set_project_resolver(None)
    yield
    session_module.set_project_resolver(None)


@pytest.fixture
def manager() -> SessionManager:
    return SessionManager()


@pytest.fixture
def live(manager) -> Session:
    sess = Session(id="s1", name="A session", kind="sdk")
    manager.add(sess)
    return sess


# ------------------------------------------------------------ event shape

def test_an_event_flattens_its_data_to_the_top_level():
    event = TranscriptEvent("text", "s1", 3, {"role": "assistant", "text": "hi"})
    assert event.to_dict() == {"type": "text", "session_id": "s1", "seq": 3,
                               "ts": event.ts, "role": "assistant", "text": "hi"}


def test_every_event_type_the_bus_folds_on_is_a_known_type():
    assert {"status", "usage", "text", "tool_use"} <= EVENT_TYPES


# ---------------------------------------- the status vocabulary, end to end

_STATIC = pathlib.Path(__file__).resolve().parent.parent / "codinian" / "remote" / "static"


def test_every_status_has_a_colour_in_the_browser_sidebar():
    """A status with no `.dot-<status>` rule gets a transparent dot: the class
    lands on the element, nothing paints it, and the row silently stops saying
    anything. Caught exactly that way when `rate_limited` was added."""
    css = (_STATIC / "styles.css").read_text()
    missing = [s.value for s in SessionStatus if f".dot-{s.value}" not in css]
    assert missing == []


def test_every_status_has_a_word_in_the_browser_sidebar():
    """`statusLabel` falls back to the raw value, so a missing entry shows
    `rate_limited` where the rest of the UI says "limit reached". Read out of
    the STATUS_LABEL block rather than the whole file, which would pass on the
    status name appearing anywhere at all."""
    app_js = (_STATIC / "app.js").read_text()
    start = app_js.index("const STATUS_LABEL = {")
    block = app_js[start:app_js.index("};", start)]
    missing = [s.value for s in SessionStatus if f"{s.value}:" not in block]
    assert missing == []


# -------------------------------------------------------------- the bus

def test_seq_starts_at_zero_and_increments_per_session(manager):
    manager.add(Session(id="a"))
    manager.add(Session(id="b"))

    assert [manager.add_event("a", "text", {}).seq for _ in range(3)] == [0, 1, 2]
    assert manager.add_event("b", "text", {}).seq == 0


def test_an_event_for_an_unknown_session_is_dropped(manager):
    assert manager.add_event("never-added", "text", {}) is None


def test_subscribers_receive_every_appended_event(manager, live):
    received = []
    manager.subscribe_events(lambda sid, payload: received.append((sid, payload)))

    manager.add_event("s1", "text", {"text": "hello"})
    assert received[0][0] == "s1"
    assert received[0][1]["text"] == "hello"
    assert received[0][1]["seq"] == 0


def test_unsubscribing_stops_delivery(manager, live):
    received = []
    callback = lambda sid, payload: received.append(payload)
    manager.subscribe_events(callback)
    manager.unsubscribe_events(callback)
    manager.add_event("s1", "text", {})
    assert received == []


def test_stored_events_replay_in_order(manager, live):
    for i in range(3):
        manager.add_event("s1", "text", {"text": str(i)})
    replayed = manager.get_events("s1")
    assert [e["text"] for e in replayed] == ["0", "1", "2"]
    assert [e["seq"] for e in replayed] == [0, 1, 2]


def test_removing_a_session_forgets_its_events_and_its_counter(manager, live):
    manager.add_event("s1", "text", {})
    manager.remove("s1")
    assert manager.get_events("s1") == []
    assert manager.get("s1") is None


def test_a_transient_event_is_broadcast_but_not_stored(manager, live):
    received = []
    manager.subscribe_events(lambda sid, payload: received.append(payload))

    manager.broadcast_transient("s1", "text_delta", {"text": "par"})

    assert received[0]["transient"] is True
    assert "seq" not in received[0]
    # A reconnecting client rebuilds from stored events; a delta has no place
    # in that ordering.
    assert manager.get_events("s1") == []


def test_a_transient_event_for_an_unknown_session_is_dropped(manager):
    received = []
    manager.subscribe_events(lambda sid, payload: received.append(payload))
    manager.broadcast_transient("nope", "text_delta", {"text": "x"})
    assert received == []


# ------------------------------------------------------- status folding

def test_a_status_event_updates_the_session(manager, live):
    manager.add_event("s1", "status", {"status": "working"})
    assert live.sdk_status is SessionStatus.WORKING
    assert live.meta()["status"] == "working"


def test_an_unrecognised_status_leaves_the_last_good_one(manager, live):
    manager.add_event("s1", "status", {"status": "working"})
    manager.add_event("s1", "status", {"status": "not-a-status"})
    assert live.sdk_status is SessionStatus.WORKING


def test_status_and_usage_events_refresh_the_session_list(manager, live):
    changes = []
    manager.on_change(lambda: changes.append(1))

    manager.add_event("s1", "text", {"text": "quiet"})
    assert changes == []

    manager.add_event("s1", "status", {"status": "done"})
    manager.add_event("s1", "usage", {"total_cost_usd": 1.0})
    assert len(changes) == 2


# -------------------------------------------------------- usage folding

def test_a_cumulative_total_is_assigned_not_added(manager, live):
    manager.add_event("s1", "usage", {"tokens_total": {"input": 900, "output": 10,
                                                       "cache_read": 0, "cache_creation": 0}})
    manager.add_event("s1", "usage", {"tokens_total": {"input": 903, "output": 12,
                                                       "cache_read": 0, "cache_creation": 0}})
    assert live.tokens["input"] == 903
    assert live.tokens["output"] == 12


def test_per_turn_counts_accumulate_when_there_is_no_cumulative_total(manager, live):
    for _ in range(2):
        manager.add_event("s1", "usage", {"tokens": {
            "input_tokens": 100, "output_tokens": 20,
            "cache_read_input_tokens": 5, "cache_creation_input_tokens": 1}})
    assert live.tokens == {"input": 200, "output": 40, "cache_read": 10, "cache_creation": 2}


def test_a_usage_event_with_nothing_usable_changes_nothing(manager, live):
    manager.add_event("s1", "usage", {"tokens": "not a dict"})
    manager.add_event("s1", "usage", {})
    assert live.tokens == {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    assert live.cost_usd == 0.0


def test_a_non_integer_token_count_is_ignored(manager, live):
    manager.add_event("s1", "usage", {"tokens": {"input_tokens": "lots",
                                                 "output_tokens": 7}})
    assert live.tokens["input"] == 0
    assert live.tokens["output"] == 7


def test_cost_is_taken_from_the_event(manager, live):
    manager.add_event("s1", "usage", {"total_cost_usd": 0.1234})
    assert live.cost_usd == 0.1234


def test_a_usage_event_without_a_cost_leaves_the_previous_one(manager, live):
    manager.add_event("s1", "usage", {"total_cost_usd": 0.5})
    manager.add_event("s1", "usage", {"tokens": {"input_tokens": 1}})
    assert live.cost_usd == 0.5


# ------------------------------------------------------------- naming

def test_renaming_by_hand_sticks(manager, live):
    assert manager.rename("s1", "  My name  ") is True
    assert live.name == "My name"
    assert live.name_is_custom is True

    assert manager.set_auto_name("s1", "A title the CLI generated") is False
    assert live.name == "My name"


def test_an_empty_rename_is_refused(manager, live):
    assert manager.rename("s1", "   ") is False
    assert live.name == "A session"


def test_renaming_an_unknown_session_reports_false(manager):
    assert manager.rename("nope", "Name") is False
    assert manager.set_auto_name("nope", "Name") is False


def test_an_auto_name_is_adopted_once_and_not_repeated(manager, live):
    assert manager.set_auto_name("s1", "Generated title") is True
    assert live.name == "Generated title"
    assert live.name_is_custom is False
    # Called after every turn, so an unchanged name must not keep notifying.
    assert manager.set_auto_name("s1", "Generated title") is False


# ------------------------------------------------------- status mapping

@pytest.mark.parametrize("status, expected", [
    (Status.RUNNING, "working"),
    (Status.IDLE, "awaiting_input"),
    (Status.STUCK, "awaiting_input"),
    (Status.DONE, "done"),
    (Status.ERROR, "error"),
])
def test_a_terminal_session_maps_its_timing_status_onto_the_protocol(status, expected):
    assert Session(kind="terminal", status=status).wire_status() == expected


def test_an_sdk_session_reports_initializing_until_an_event_says_otherwise():
    sess = Session(kind="sdk")
    assert sess.wire_status() == "initializing"
    sess.sdk_status = SessionStatus.AWAITING_APPROVAL
    assert sess.wire_status() == "awaiting_approval"


def test_recording_output_brings_an_idle_session_back_to_running(manager):
    manager.add(Session(id="t1", kind="terminal", status=Status.IDLE))
    manager.record_output("t1")
    assert manager.get("t1").status is Status.RUNNING
    assert manager.get("t1").last_output_at is not None


def test_recording_output_does_not_revive_a_finished_session(manager):
    manager.add(Session(id="t1", kind="terminal", status=Status.DONE))
    manager.record_output("t1")
    assert manager.get("t1").status is Status.DONE


# ---------------------------------------------------------------- meta

def test_meta_carries_the_fields_the_protocol_names(manager, live):
    meta = live.meta()
    assert set(meta) == {"id", "name", "name_is_custom", "workdir", "kind",
                         "status", "created_at", "sdk_session_id",
                         "permission_mode", "cost_usd", "tokens",
                         "totals_cover_this_run_only", "project_id"}
    assert meta["created_at"] == live.created_at.isoformat()


def test_meta_copies_the_token_dict_rather_than_sharing_it(live):
    meta = live.meta()
    meta["tokens"]["input"] = 999
    assert live.tokens["input"] == 0


def test_project_id_is_none_until_a_resolver_is_installed(live):
    assert live.project_id() is None


def test_project_id_is_derived_on_every_read(live):
    lookup = {}
    session_module.set_project_resolver(lambda workdir: lookup.get(workdir))
    assert live.project_id() is None

    # Registering a folder claims the sessions already running inside it.
    lookup[live.workdir] = "abc12345"
    assert live.project_id() == "abc12345"


def test_permission_modes_are_the_set_the_sdk_accepts():
    assert session_module.PERMISSION_MODES == frozenset({
        "default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"})


# ------------------------------------------------------------- injection

def test_injects_drain_in_order_and_only_once(manager):
    manager.queue_inject("s1", "first")
    manager.queue_inject("s2", "second")
    assert manager.drain_injects() == [("s1", "first"), ("s2", "second")]
    assert manager.drain_injects() == []


def test_snapshot_describes_every_session(manager):
    manager.add(Session(id="s1", name="One", created_at=datetime(2026, 8, 24)))
    snapshot = manager.snapshot()
    assert snapshot[0]["id"] == "s1"
    assert snapshot[0]["created_at"] == "2026-08-24T00:00:00"
    assert snapshot[0]["last_output_at"] is None
