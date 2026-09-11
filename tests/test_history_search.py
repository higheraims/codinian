"""Full-text search across stored transcripts.

The scan is only fast because it does not parse: a raw substring test over the
file's bytes rejects most files, and JSON is parsed only for lines that
matched. These tests pin the behaviour that rests on that, including the case
where the needle is in a uuid rather than in anything anyone wrote.
"""

from __future__ import annotations

import json

import pytest

import claude_history
import history_search


@pytest.fixture(autouse=True)
def projects_dir(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    directory.mkdir()
    monkeypatch.setattr(claude_history, "PROJECTS_DIR", directory)
    return directory


@pytest.fixture
def write_transcript(projects_dir):
    def _write(entries, session_id="aaaa1111", project="-proj", cwd="/home/ntyler/proj"):
        folder = projects_dir / project
        folder.mkdir(exist_ok=True)
        path = folder / f"{session_id}.jsonl"
        path.write_text("".join(
            json.dumps({"cwd": cwd, **entry}) + "\n" for entry in entries))
        return path
    return _write


def user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def assistant(blocks, **extra):
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}, **extra}


def test_a_match_reports_the_session_and_a_readable_snippet(write_transcript):
    write_transcript([user("Fix the ZeroDivisionError in the report"),
                      assistant([{"type": "text", "text": "Looking at it"}])])

    hits = history_search.search("zerodivisionerror")
    assert len(hits) == 1
    assert hits[0].session_id == "aaaa1111"
    assert hits[0].cwd == "/home/ntyler/proj"
    assert "ZeroDivisionError" in hits[0].snippets[0].text


def test_the_search_is_case_insensitive(write_transcript):
    write_transcript([user("The Traceback is long")])
    assert history_search.search("TRACEBACK")
    assert history_search.search("traceback")


def test_a_mistyped_regex_returns_nothing_rather_than_raising(write_transcript):
    # Substring, not regex: this backs a search box.
    write_transcript([user("plain text")])
    assert history_search.search("[unclosed(") == []


def test_an_empty_query_returns_nothing(write_transcript):
    write_transcript([user("plain text")])
    assert history_search.search("   ") == []


def test_match_count_counts_every_occurrence_in_the_file(write_transcript):
    write_transcript([user("needle needle"), assistant([{"type": "text", "text": "needle"}])])
    assert history_search.search("needle")[0].match_count == 3


def test_snippets_are_capped_per_session(write_transcript):
    write_transcript([user(f"needle number {i}") for i in range(20)])
    hits = history_search.search("needle")
    assert len(hits[0].snippets) == history_search.MAX_SNIPPETS_PER_SESSION
    assert hits[0].match_count == 20


def test_a_match_only_in_metadata_produces_no_hit(write_transcript):
    # The needle is in a uuid, not in anything anyone wrote.
    write_transcript([user("nothing to see", uuid="deadbeef-0000")])
    assert history_search.search("deadbeef") == []


def test_a_snippet_is_labelled_by_what_the_block_is(write_transcript):
    write_transcript([
        assistant([{"type": "thinking", "thinking": "the needle is here"}]),
        assistant([{"type": "tool_use", "name": "Bash",
                    "input": {"command": "grep needle"}}]),
        user([{"type": "tool_result", "content": "needle found in output"}]),
    ])
    labels = [s.role for s in history_search.search("needle")[0].snippets]
    assert labels == ["thinking", "Bash call", "tool result"]


def test_a_long_line_is_excerpted_around_the_match(write_transcript):
    filler = "x" * 500
    write_transcript([user(f"{filler} needle {filler}")])
    snippet = history_search.search("needle")[0].snippets[0].text

    assert "needle" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) < 2 * history_search.CONTEXT + 30


def test_a_multi_line_match_is_flattened_to_one_line(write_transcript):
    write_transcript([user("first line\n   needle here\nlast line")])
    assert "\n" not in history_search.search("needle")[0].snippets[0].text


def test_results_are_newest_first(write_transcript):
    import os
    older = write_transcript([user("needle in the old one")], session_id="old00000")
    newer = write_transcript([user("needle in the new one")], session_id="new00000",
                             project="-other")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))

    assert [h.session_id for h in history_search.search("needle")] == ["new00000", "old00000"]


def test_the_limit_caps_the_number_of_sessions(write_transcript):
    for i in range(4):
        write_transcript([user("needle")], session_id=f"sess{i}", project=f"-p{i}")
    assert len(history_search.search("needle", limit=2)) == 2


def test_a_search_can_be_confined_to_one_project_tree(write_transcript, tmp_path):
    (tmp_path / "wanted" / "sub").mkdir(parents=True)
    (tmp_path / "wanted-other").mkdir()
    write_transcript([user("needle inside")], session_id="inside00",
                     cwd=str(tmp_path / "wanted" / "sub"))
    write_transcript([user("needle next door")], session_id="outside0", project="-x",
                     cwd=str(tmp_path / "wanted-other"))

    hits = history_search.search("needle", under=tmp_path / "wanted")
    assert [h.session_id for h in hits] == ["inside00"]


def test_a_subagent_transcript_is_not_reported_as_a_second_session(write_transcript):
    path = write_transcript([user("needle in the parent")])
    subagents = path.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(user("needle in the subagent")) + "\n")

    # Its id is not one the resume path can open, and its text is the parent's
    # work seen from inside.
    assert [h.session_id for h in history_search.search("needle")] == ["aaaa1111"]


def test_an_unparseable_line_does_not_stop_the_scan(write_transcript):
    path = write_transcript([user("needle in a good line")])
    path.write_text("{not json but contains needle\n" + path.read_text())
    assert len(history_search.search("needle")) == 1


def test_to_dict_is_json_ready(write_transcript):
    write_transcript([user("needle")])
    as_dict = history_search.search("needle")[0].to_dict()
    assert set(as_dict) == {"session_id", "cwd", "title", "mtime", "match_count", "snippets"}
    json.dumps(as_dict)


def test_a_missing_projects_directory_yields_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_history, "PROJECTS_DIR", tmp_path / "not-there")
    assert history_search.search("needle") == []
