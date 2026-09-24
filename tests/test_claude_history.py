"""Reading the CLI's stored JSONL transcripts.

Two things are worth pinning. A session id arrives straight off a URL and is
interpolated into a glob pattern, so `*` must not be a valid id. And the event
mapping has to land on the same shapes the live SDK driver produces, or seeded
history renders differently from the turn that follows it.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from codinian import claude_history

SESSION = "11111111-2222-3333-4444-555555555555"
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


@pytest.fixture(autouse=True)
def projects_dir(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    directory.mkdir()
    monkeypatch.setattr(claude_history, "PROJECTS_DIR", directory)
    return directory


@pytest.fixture
def write_transcript(projects_dir):
    def _write(entries, session_id=SESSION, project="-home-ntyler-Projects-codinian"):
        folder = projects_dir / project
        folder.mkdir(exist_ok=True)
        path = folder / f"{session_id}.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return path
    return _write


def user(content, **extra):
    return {"type": "user", "cwd": "/home/ntyler/Projects/codinian",
            "message": {"role": "user", "content": content}, **extra}


def assistant(content, **extra):
    return {"type": "assistant", "message": {"role": "assistant", "content": content}, **extra}


# ------------------------------------------------------------ id safety

@pytest.mark.parametrize("bad_id", [
    "*", "?", "[a-z]", "../../../etc/passwd", "a/b", "a\\b", "", "x" * 65,
    "with space", "semi;colon", ".",
])
def test_an_id_that_is_not_a_plain_id_resolves_to_nothing(write_transcript, bad_id):
    write_transcript([user("hello")])
    assert claude_history.transcript_path(bad_id) is None


def test_a_wildcard_does_not_match_whichever_transcript_comes_back_first(write_transcript):
    write_transcript([user("hello")])
    # Unchecked, "*" was a valid id. Confinement never depended on this, but a
    # lookup by id should not answer for an id nobody has.
    assert claude_history.transcript_path("*") is None
    assert claude_history.transcript_path(SESSION) is not None


def test_a_real_id_is_found_under_whichever_project_directory_holds_it(write_transcript):
    path = write_transcript([user("hello")], project="-some-other-project")
    assert claude_history.transcript_path(SESSION) == path


def test_an_unknown_id_resolves_to_none(write_transcript):
    write_transcript([user("hello")])
    assert claude_history.transcript_path("99999999-0000-0000-0000-000000000000") is None


@pytest.mark.parametrize("bad_agent", ["../escape", "a/b", "", "x" * 65, "*"])
def test_a_subagent_id_is_checked_before_it_lands_in_a_filename(write_transcript, bad_agent):
    path = write_transcript([user("hello")])
    subagents = path.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc123.jsonl").write_text("")

    assert claude_history.subagent_path(SESSION, bad_agent) is None
    assert claude_history.subagent_path(SESSION, "abc123") is not None


def test_a_session_with_no_subagents_has_no_subagent_directory(write_transcript):
    write_transcript([user("hello")])
    assert claude_history.subagent_dir(SESSION) is None


# ------------------------------------------- the records that pair id to call


@pytest.fixture
def write_subagent_meta(write_transcript):
    """One `agent-<id>.meta.json`, the record the CLI files as soon as a
    subagent exists -- before the `Agent` call that started it returns."""
    path = write_transcript([user("hello")])
    directory = path.with_suffix("") / "subagents"
    directory.mkdir(parents=True, exist_ok=True)

    def _write(agent_id, mtime=None, **record):
        meta = directory / f"agent-{agent_id}.meta.json"
        meta.write_text(json.dumps(record))
        if mtime is not None:
            os.utime(meta, (mtime, mtime))
        return meta
    return _write


def test_a_record_pairs_an_agent_id_with_the_call_that_started_it(write_subagent_meta):
    write_subagent_meta("abc123", toolUseId="toolu_1", description="Find the leak",
                        agentType="claude", model="sonnet")
    assert claude_history.list_subagents(SESSION) == [
        {"agent_id": "abc123", "tool_use_id": "toolu_1",
         "description": "Find the leak", "agent_type": "claude",
         "model": "sonnet"},
    ]


def test_records_come_back_newest_first(write_subagent_meta):
    """A call retried after a failure has two records. The caller takes the
    first match, so the first has to be the most recent attempt."""
    write_subagent_meta("older", mtime=1_000, toolUseId="toolu_1")
    write_subagent_meta("newer", mtime=2_000, toolUseId="toolu_1")
    assert [r["agent_id"] for r in claude_history.list_subagents(SESSION)] == [
        "newer", "older"]


def test_a_record_missing_its_optional_fields_still_reports_the_id(write_subagent_meta):
    write_subagent_meta("abc123", toolUseId="toolu_1")
    assert claude_history.list_subagents(SESSION) == [
        {"agent_id": "abc123", "tool_use_id": "toolu_1"}]


def test_one_unreadable_record_does_not_cost_the_others(write_subagent_meta):
    write_subagent_meta("good", mtime=2_000, toolUseId="toolu_1")
    write_subagent_meta("bad", mtime=1_000)
    (claude_history.subagent_dir(SESSION) / "agent-bad.meta.json").write_text("{not json")
    assert [r["agent_id"] for r in claude_history.list_subagents(SESSION)] == ["good"]


@pytest.mark.parametrize("bad_agent", ["a.b", "a b", "x" * 65])
def test_an_id_that_would_not_pass_the_filename_guard_is_skipped(write_subagent_meta,
                                                                 bad_agent):
    """These ids come off a filename rather than going into one, so nothing
    unsafe can be built here -- but they are handed to callers that do build
    paths from them, and are held to the same rule `subagent_path` applies."""
    write_subagent_meta(bad_agent, toolUseId="toolu_1")
    assert claude_history.list_subagents(SESSION) == []


def test_a_session_with_no_subagents_reports_none(write_transcript):
    write_transcript([user("hello")])
    assert claude_history.list_subagents(SESSION) == []


# --------------------------------------------------------- prior sessions

def test_a_prior_session_reports_its_cwd_and_first_message(write_transcript):
    write_transcript([user("Fix the scrolling bug"), assistant([{"type": "text", "text": "ok"}])])
    found = claude_history.list_prior_sessions()
    assert len(found) == 1
    assert found[0].id == SESSION
    assert found[0].cwd == "/home/ntyler/Projects/codinian"
    assert found[0].title == "Fix the scrolling bug"


def test_a_session_with_no_user_message_is_skipped(write_transcript):
    write_transcript([assistant([{"type": "text", "text": "unprompted"}])])
    assert claude_history.list_prior_sessions() == []


def test_a_session_whose_only_message_is_a_tag_reads_as_having_none(write_transcript):
    write_transcript([user("<command-name>/clear</command-name>")])
    assert claude_history.list_prior_sessions()[0].title == "(no messages)"


def test_a_long_first_message_is_cut_to_one_line(write_transcript):
    write_transcript([user("x" * 300 + "\nsecond line")])
    title = claude_history.list_prior_sessions()[0].title
    assert title == "x" * 120


def test_an_unparseable_line_does_not_cost_the_session(write_transcript, projects_dir):
    path = write_transcript([user("good line")])
    path.write_text("{not json\n" + path.read_text())
    assert claude_history.list_prior_sessions()[0].title == "good line"


def test_sessions_come_back_newest_first(write_transcript):
    first = write_transcript([user("older")], session_id="a" * 8)
    second = write_transcript([user("newer")], session_id="b" * 8)
    import os
    os.utime(first, (1_700_000_000, 1_700_000_000))
    os.utime(second, (1_800_000_000, 1_800_000_000))
    assert [s.id for s in claude_history.list_prior_sessions()] == ["b" * 8, "a" * 8]


def test_under_matches_path_components_not_string_prefixes(write_transcript, tmp_path):
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (tmp_path / "proj-other").mkdir()

    inside = user("in the project")
    inside["cwd"] = str(proj / "sub")
    outside = user("next door")
    outside["cwd"] = str(tmp_path / "proj-other")
    write_transcript([inside], session_id="a" * 8)
    write_transcript([outside], session_id="b" * 8, project="-other")

    found = claude_history.list_prior_sessions(under=proj)
    assert [s.id for s in found] == ["a" * 8]


def test_the_limit_is_applied(write_transcript):
    for i in range(5):
        write_transcript([user(f"session {i}")], session_id=f"{i}" * 8)
    assert len(claude_history.list_prior_sessions(limit=2)) == 2


def test_a_missing_projects_directory_yields_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_history, "PROJECTS_DIR", tmp_path / "not-there")
    assert claude_history.list_prior_sessions() == []
    assert claude_history.transcript_path(SESSION) is None


# ------------------------------------------------------- permission mode

def test_the_last_recorded_permission_mode_wins(write_transcript):
    write_transcript([
        user("one", permissionMode="default"),
        user("two", permissionMode="plan"),
        user("three", permissionMode="acceptEdits"),
    ])
    assert claude_history.last_permission_mode(SESSION) == "acceptEdits"


def test_a_transcript_recording_no_mode_returns_none(write_transcript):
    write_transcript([user("one")])
    assert claude_history.last_permission_mode(SESSION) is None


def test_an_empty_mode_is_not_taken_as_an_answer(write_transcript):
    write_transcript([user("one", permissionMode="plan"), user("two", permissionMode="")])
    assert claude_history.last_permission_mode(SESSION) == "plan"


# ------------------------------------------------------------ generated name

def test_the_newest_ai_title_becomes_the_name(write_transcript):
    write_transcript([
        user("hello"),
        {"type": "ai-title", "aiTitle": "First guess"},
        {"type": "ai-title", "aiTitle": "  A better title  "},
    ])
    assert claude_history.generated_name(SESSION) == "A better title"


def test_without_an_ai_title_the_first_message_stands_in(write_transcript):
    write_transcript([user("Investigate the flaky test")])
    assert claude_history.generated_name(SESSION) == "Investigate the flaky test"


def test_a_long_name_is_truncated_with_an_ellipsis(write_transcript):
    write_transcript([user("word " * 40)])
    name = claude_history.generated_name(SESSION)
    assert len(name) == 60
    assert name.endswith("…")


def test_a_session_with_no_messages_yet_has_no_name(write_transcript):
    write_transcript([])
    assert claude_history.generated_name(SESSION) is None


# ------------------------------------------------------------ event mapping

def test_assistant_text_thinking_and_tool_calls_become_events(write_transcript):
    write_transcript([assistant([
        {"type": "thinking", "thinking": "considering"},
        {"type": "text", "text": "Here is the answer"},
        {"type": "tool_use", "id": "toolu_01", "name": "Read", "input": {"file_path": "/x"}},
    ])])

    events = claude_history.events_for_session(SESSION)
    assert [etype for etype, _ in events] == ["thinking", "text", "tool_use"]
    assert events[1][1] == {"role": "assistant", "text": "Here is the answer"}
    assert events[2][1] == {"tool_use_id": "toolu_01", "name": "Read",
                            "input": {"file_path": "/x"}}


def test_empty_text_and_thinking_blocks_are_dropped(write_transcript):
    write_transcript([assistant([{"type": "text", "text": "   "},
                                 {"type": "thinking", "thinking": ""}])])
    assert claude_history.events_for_session(SESSION) == []


def test_a_user_turn_is_labelled_as_typed_by_the_operator(write_transcript):
    write_transcript([user("do the thing")])
    etype, data = claude_history.events_for_session(SESSION)[0]
    assert etype == "text"
    assert data == {"role": "user", "text": "do the thing", "source": "operator"}


def test_a_tool_result_carries_its_id_and_content(write_transcript):
    write_transcript([user([{"type": "tool_result", "tool_use_id": "toolu_01",
                             "content": "file contents", "is_error": False}])])
    etype, data = claude_history.events_for_session(SESSION)[0]
    assert etype == "tool_result"
    assert data["tool_use_id"] == "toolu_01"
    assert data["content"] == "file contents"
    assert data["is_error"] is False


def test_bookkeeping_and_meta_lines_are_skipped(write_transcript):
    write_transcript([
        {"type": "file-history-snapshot", "message": {"content": "noise"}},
        {"type": "queue-operation", "message": {"content": "noise"}},
        user("injected by the CLI", isMeta=True),
        user("typed by a person"),
    ])
    events = claude_history.events_for_session(SESSION)
    assert [data["text"] for _e, data in events] == ["typed by a person"]


def test_sidechain_entries_are_left_out_of_the_main_thread(write_transcript):
    write_transcript([user("main thread"),
                      assistant([{"type": "text", "text": "subagent chatter"}],
                                isSidechain=True)])
    events = claude_history.events_for_session(SESSION)
    assert [data["text"] for _e, data in events] == ["main thread"]


def test_an_over_long_transcript_is_truncated_with_a_note(write_transcript):
    write_transcript([user(f"message {i}") for i in range(10)])
    events = claude_history.events_for_session(SESSION, limit=4)

    assert len(events) == 5
    assert events[0][0] == "system"
    assert events[0][1]["subtype"] == "history_truncated"
    assert "6 earlier events" in events[0][1]["data"]["message"]
    assert events[-1][1]["text"] == "message 9"


def test_a_stored_image_becomes_a_reference_back_to_this_file(write_transcript):
    encoded = base64.b64encode(PNG).decode()
    write_transcript([user([{
        "type": "tool_result", "tool_use_id": "toolu_img",
        "content": [{"type": "image",
                     "source": {"type": "base64", "media_type": "image/png",
                                "data": encoded}}],
    }])])

    _etype, data = claude_history.events_for_session(SESSION)[0]
    source = data["content"][0]["source"]
    assert source["type"] == "codinian_ref"
    assert source["path"] == f"/api/history/{SESSION}/image/toolu_img/0"
    assert encoded not in json.dumps(data)


def test_the_referenced_image_is_readable_back_out_of_the_transcript(write_transcript):
    encoded = base64.b64encode(PNG).decode()
    write_transcript([user([{
        "type": "tool_result", "tool_use_id": "toolu_img",
        "content": [{"type": "image",
                     "source": {"type": "base64", "media_type": "image/png",
                                "data": encoded}}],
    }])])

    assert claude_history.image_from_transcript(SESSION, "toolu_img", 0) == ("image/png", PNG)


@pytest.mark.parametrize("tool_use_id, index", [
    ("toolu_img", 1),      # past the end of the content list
    ("toolu_img", -1),     # not an index at all
    ("toolu_missing", 0),  # no such tool call in this transcript
])
def test_an_image_that_does_not_resolve_returns_none(write_transcript, tool_use_id, index):
    encoded = base64.b64encode(PNG).decode()
    write_transcript([user([{
        "type": "tool_result", "tool_use_id": "toolu_img",
        "content": [{"type": "image",
                     "source": {"type": "base64", "media_type": "image/png",
                                "data": encoded}}],
    }])])
    assert claude_history.image_from_transcript(SESSION, tool_use_id, index) is None


def test_a_subagent_call_carries_the_link_to_its_transcript(write_transcript):
    write_transcript([{
        "type": "user",
        "cwd": "/tmp",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_agent", "content": "done"}]},
        "toolUseResult": {"agentId": "abc123", "description": "Find the bug",
                          "resolvedModel": "claude-sonnet-5", "status": "completed"},
    }])

    _etype, data = claude_history.events_for_session(SESSION)[0]
    assert data["agent_id"] == "abc123"
    assert data["agent_description"] == "Find the bug"
    assert data["agent_model"] == "claude-sonnet-5"
    assert data["agent_status"] == "completed"


def test_a_subagent_transcript_keeps_its_own_sidechain_turns(write_transcript):
    path = write_transcript([user("parent")])
    subagents = path.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc123.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [
        user("Go and find the bug.", isSidechain=True),
        assistant([{"type": "text", "text": "Found it."}], isSidechain=True),
    ]))

    events = claude_history.subagent_events(SESSION, "abc123")
    assert [etype for etype, _ in events] == ["text", "text"]
    # The opening turn is the briefing the parent handed it, not conversation.
    assert events[0][1]["source"] == "briefing"
    assert events[1][1]["text"] == "Found it."


def test_a_session_with_no_transcript_seeds_nothing():
    assert claude_history.events_for_session("99999999-0000-0000-0000-000000000000") == []
    assert claude_history.subagent_events(SESSION, "abc123") == []


# ------------------------------- what the user typed, on replay (ISSUE-074)

def user_texts(session_id=SESSION):
    return [d for kind, d in claude_history.events_for_session(session_id)
            if kind == "text" and d.get("role") == "user"]


def test_a_typed_turn_replays_as_the_users_own(write_transcript):
    write_transcript([user([{"type": "text", "text": "Address issues 46, 47, 48"}])])
    assert [d["source"] for d in user_texts()] == ["operator"]


def test_a_task_notification_is_not_drawn_as_something_the_user_typed(
        write_transcript):
    # The whole of ISSUE-074. A background agent reporting back is an ordinary
    # user turn as far as the transcript is concerned, and it is not marked
    # isMeta, so before this it was labelled `operator` and its raw XML
    # envelope was pasted into the transcript verbatim.
    write_transcript([
        user([{"type": "text", "text": "Address issues 46, 47, 48"}]),
        user([{"type": "text",
               "text": "<task-notification>\n<task-id>a9d0</task-id>\n"
                       "<status>completed</status>\n</task-notification>"}],
             origin={"kind": "task-notification"}),
    ])
    assert [d["source"] for d in user_texts()] == ["operator", "injected"]


def test_any_harness_generated_origin_folds_not_only_task_notifications(
        write_transcript):
    # `origin` is the marker, not the one kind of it seen so far, so a kind
    # nobody has met yet does not arrive as a wall of XML.
    write_transcript([user([{"type": "text", "text": "<whatever/>"}],
                           origin={"kind": "something-later"})])
    assert [d["source"] for d in user_texts()] == ["injected"]


def test_an_origin_that_is_not_a_record_is_left_alone(write_transcript):
    # Defensive only: a scalar `origin` is not the harness marker, and a turn
    # must never be hidden because a field had an unexpected shape.
    write_transcript([user([{"type": "text", "text": "typed this"}], origin="user")])
    assert [d["source"] for d in user_texts()] == ["operator"]


def test_a_turn_the_harness_marks_human_is_the_users_own(write_transcript):
    # `origin` alone is not the marker. The CLI stamps a typed message
    # `{"kind": "human"}`, and folding on the record's presence hid 21 real
    # messages across the transcripts this was checked against (ISSUE-075).
    write_transcript([user([{"type": "text", "text": "yes, commit it"}],
                           origin={"kind": "human"})])
    assert [d["source"] for d in user_texts()] == ["operator"]


def test_a_slash_command_envelope_is_not_drawn_as_a_typed_turn(write_transcript):
    # Running /model writes an isMeta caveat and then the envelope as its
    # child. The caveat is dropped, so the envelope's parentage is the only
    # thing left marking it (ISSUE-075).
    write_transcript([
        {"type": "user", "uuid": "caveat-1", "isMeta": True,
         "cwd": "/home/ntyler/Projects/codinian",
         "message": {"role": "user",
                     "content": [{"type": "text",
                                  "text": "<local-command-caveat>x</local-command-caveat>"}]}},
        user([{"type": "text", "text": "<command-name>/model</command-name>"}],
             uuid="cmd-1", parentUuid="caveat-1"),
        user([{"type": "text", "text": "and now a real question"}],
             uuid="typed-1", parentUuid="cmd-1"),
    ])
    assert [d["source"] for d in user_texts()] == ["injected", "operator"]


def test_a_turn_descended_from_nothing_meta_is_left_alone(write_transcript):
    write_transcript([user([{"type": "text", "text": "plain"}],
                           uuid="u1", parentUuid="not-a-meta-uuid")])
    assert [d["source"] for d in user_texts()] == ["operator"]
