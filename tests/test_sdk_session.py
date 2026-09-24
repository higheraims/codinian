"""The permission gate, and the two helpers that shape what a session reports.

`_auto_decision` is the whole of what a permission mode means in practice: it
decides, before anyone is asked, whether a tool call is allowed here, handed to
the CLI to decide, or put to the user. Getting it wrong is either an approval
card for every `cat` or a session that edits files nobody approved.

Nothing here starts a turn loop or talks to the CLI. Constructing an SdkSession
connects to nothing; `start()` is what would.
"""

from __future__ import annotations

import asyncio
import functools

import pytest

from codinian import sdk_session
from claude_agent_sdk import SystemMessage

from codinian.sdk_session import SdkSession
from codinian.session import Session, SessionManager


def async_test(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


@pytest.fixture
def manager() -> SessionManager:
    mgr = SessionManager()
    mgr.add(Session(id="s1", kind="sdk"))
    return mgr


def make_session(manager, mode="default") -> SdkSession:
    return SdkSession(manager, "s1", "/tmp/proj", permission_mode=mode)


def hook_input(name="Bash", tool_use_id="toolu_01", **tool_input):
    return {"tool_name": name, "tool_use_id": tool_use_id, "tool_input": tool_input}


# ------------------------------------------------------- the decision table

@pytest.mark.parametrize("tool", ["Bash", "Read", "Edit", "WebFetch", "ExitPlanMode"])
def test_bypass_permissions_allows_everything(manager, tool):
    assert make_session(manager, "bypassPermissions")._auto_decision(tool) == "allow"


@pytest.mark.parametrize("tool", sorted(sdk_session.EDIT_TOOLS))
def test_accept_edits_allows_an_edit(manager, tool):
    assert make_session(manager, "acceptEdits")._auto_decision(tool) == "allow"


@pytest.mark.parametrize("tool", ["Bash", "WebFetch", "Task"])
def test_accept_edits_still_asks_about_anything_else(manager, tool):
    # This is the difference between acceptEdits and bypassPermissions.
    assert make_session(manager, "acceptEdits")._auto_decision(tool) is None


@pytest.mark.parametrize("mode", sorted(sdk_session.MODE_DECIDES_ITSELF))
def test_a_mode_the_cli_enforces_defers_rather_than_answering(manager, mode):
    assert make_session(manager, mode)._auto_decision("Bash") == "defer"


def test_plan_mode_still_puts_the_plan_itself_to_the_user(manager):
    # Deferring would hand the plan to a CLI expecting a client that approves
    # plans, and the turn would park on a question nobody is shown.
    assert make_session(manager, "plan")._auto_decision("ExitPlanMode") is None


@pytest.mark.parametrize("mode", ["dontAsk", "auto"])
def test_exit_plan_mode_is_asked_about_in_every_deferring_mode(manager, mode):
    assert make_session(manager, mode)._auto_decision("ExitPlanMode") is None


@pytest.mark.parametrize("tool", ["Bash", "Read", "Edit"])
def test_the_default_mode_asks_about_everything(manager, tool):
    assert make_session(manager, "default")._auto_decision(tool) is None


def test_every_permission_mode_has_a_defined_answer(manager):
    from codinian.session import PERMISSION_MODES
    for mode in PERMISSION_MODES:
        assert make_session(manager, mode)._auto_decision("Bash") in (None, "allow", "defer")


# ------------------------------------------------------------ the hook

@async_test
async def test_the_question_tool_is_allowed_before_any_mode_is_consulted():
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    # A question is not a permission, and no mode should answer it for the
    # user. It is put to them by the permission callback instead.
    session = make_session(manager, "default")
    result = await session._pre_tool_use(
        hook_input(name=sdk_session.QUESTION_TOOL), "toolu_01", None)

    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert manager.get_events("s1") == []


@async_test
async def test_an_allowed_call_says_so_in_the_transcript():
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = make_session(manager, "bypassPermissions")

    result = await session._pre_tool_use(hook_input(name="Bash", command="ls"),
                                         "toolu_01", None)

    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "bypassPermissions" in result["hookSpecificOutput"]["permissionDecisionReason"]
    # A call that runs without an approval card is otherwise indistinguishable
    # from one the user forgot they approved.
    note = manager.get_events("s1")[0]
    assert note["type"] == "permission_note"
    assert note["mode"] == "bypassPermissions"
    assert note["outcome"] == "allow"
    assert note["name"] == "Bash"


@async_test
async def test_a_deferred_call_returns_no_decision_at_all():
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = make_session(manager, "auto")

    result = await session._pre_tool_use(hook_input(name="Bash"), "toolu_01", None)

    # No hookSpecificOutput, so the CLI applies the mode itself rather than
    # reading a decision out of this hook.
    assert result == {}
    assert manager.get_events("s1")[0]["outcome"] == "defer"


# ------------------------------------------------------------ hook timeout

def test_the_hook_timeout_is_a_day_rather_than_the_cli_default():
    # Every approval waits inside the hook, so the CLI's 600 second default
    # put a ten-minute ceiling on how long a person had to answer one.
    assert sdk_session.HOOK_TIMEOUT_SECONDS == 24 * 60 * 60


# ---------------------------------------------------------- usage totals

def test_token_totals_are_summed_across_every_model_a_session_used():
    totals = sdk_session._model_usage_totals({
        "claude-opus-5": {"inputTokens": 900, "outputTokens": 100,
                          "cacheReadInputTokens": 10, "cacheCreationInputTokens": 5},
        "claude-haiku-4-5": {"inputTokens": 3, "outputTokens": 2},
    })
    assert totals == {"input": 903, "output": 102, "cache_read": 10, "cache_creation": 5}


def test_a_missing_count_reads_as_zero_rather_than_raising():
    assert sdk_session._model_usage_totals({"m": {}}) == {
        "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}


def test_a_null_count_reads_as_zero():
    assert sdk_session._model_usage_totals({"m": {"inputTokens": None}})["input"] == 0


def test_an_entry_that_is_not_a_dict_is_skipped():
    assert sdk_session._model_usage_totals({"m": "nonsense",
                                            "n": {"inputTokens": 7}})["input"] == 7


@pytest.mark.parametrize("value", [None, {}, "not a dict", 42])
def test_nothing_usable_gives_no_totals(value):
    assert sdk_session._model_usage_totals(value) is None


# ------------------------------------------------------------- quoting

def test_a_cancelled_message_is_quoted_on_one_short_line():
    assert sdk_session._one_line("  hello   there  ") == '"hello there"'
    assert sdk_session._one_line("first\nsecond") == '"first second"'


def test_a_long_message_is_cut_with_an_ellipsis():
    quoted = sdk_session._one_line("word " * 40)
    assert quoted.endswith('…"')
    assert len(quoted) == sdk_session.CANCELLED_QUOTE_CHARS + 2


def test_an_empty_message_still_quotes():
    assert sdk_session._one_line("") == '""'


# ------------------------------------------- the usage limit that ends a turn


class FakeRateLimitInfo:
    """Enough of the SDK's `RateLimitInfo` for `_note_limit_block` to read."""

    def __init__(self, status="rejected", resets_at=1_800_000_000,
                 rate_limit_type="five_hour", overage_status=None):
        self.status = status
        self.resets_at = resets_at
        self.rate_limit_type = rate_limit_type
        self.overage_status = overage_status


def emitted(session) -> list[tuple[str, dict]]:
    """Collect what the session reports instead of putting it on a manager."""
    seen: list[tuple[str, dict]] = []
    session._emit = lambda etype, data: seen.append((etype, data))
    return seen


def test_a_rejected_limit_with_a_reset_time_is_held(manager):
    session = make_session(manager)
    session._note_limit_block(FakeRateLimitInfo())
    assert session._limit_block == {"rate_limit_type": "five_hour",
                                    "resets_at": 1_800_000_000}


@pytest.mark.parametrize("info", [
    # A warning is not a closed window.
    FakeRateLimitInfo(status="allowed_warning"),
    FakeRateLimitInfo(status="allowed"),
    # A wait with no end is not one a card can offer.
    FakeRateLimitInfo(resets_at=None),
    # Overage keeps the session working, so nothing was stopped.
    FakeRateLimitInfo(overage_status="allowed"),
])
def test_a_limit_that_does_not_stop_the_turn_is_not_held(manager, info):
    session = make_session(manager)
    session._note_limit_block(info)
    assert session._limit_block is None


def test_a_blocked_turn_reports_the_window_and_the_prompt_it_lost(manager):
    session = make_session(manager)
    session._last_query_text = "run the suite"
    session._note_limit_block(FakeRateLimitInfo())
    seen = emitted(session)
    session._report_limit_block()
    assert seen == [("rate_limit_block", {"rate_limit_type": "five_hour",
                                          "resets_at": 1_800_000_000,
                                          "prompt": "run the suite",
                                          "subagents": []})]


def test_a_turn_that_merely_ended_reports_nothing(manager):
    session = make_session(manager)
    seen = emitted(session)
    session._report_limit_block()
    assert seen == []


def test_one_limit_is_reported_once(manager):
    session = make_session(manager)
    session._note_limit_block(FakeRateLimitInfo())
    seen = emitted(session)
    session._report_limit_block()
    session._report_limit_block()
    assert len(seen) == 1


def test_the_model_producing_under_a_limit_clears_the_block(manager):
    """A `rejected` reading also lands on a session that is merely idle, and on
    the turn after the window reopened. An assistant message is the proof that
    this turn ran, so the card it would otherwise leave is dropped."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    session = make_session(manager)
    session._note_limit_block(FakeRateLimitInfo())
    emitted(session)
    session._handle_message(
        AssistantMessage(content=[TextBlock(text="working on it")],
                         model="claude-opus-5")
    )
    assert session._limit_block is None


# ------------------------------------------------- the id a subagent is known by


def agent_result_message(tool_use_result=None, tool_use_id="toolu_agent"):
    """A `UserMessage` closing a tool call, with the result record the CLI
    files beside it. That record is the field Codinian used to drop."""
    from claude_agent_sdk import ToolResultBlock, UserMessage
    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content="done",
                                 is_error=False)],
        tool_use_result=tool_use_result,
    )


def test_a_live_agent_result_carries_the_id_the_subagent_is_known_by(manager):
    """The id is what `SendMessage` takes to continue an agent, and what fetches
    its transcript. It arrives on the result record, not in the result text."""
    session = make_session(manager)
    seen = emitted(session)
    session._handle_message(agent_result_message(
        {"agentId": "a1b2c3", "description": "Find the leak",
         "resolvedModel": "claude-opus-5", "status": "completed"}))
    assert seen == [("tool_result", {
        "tool_use_id": "toolu_agent",
        "is_error": False,
        "content": "done",
        "agent_id": "a1b2c3",
        "agent_description": "Find the leak",
        "agent_model": "claude-opus-5",
        "agent_status": "completed",
    })]


def test_an_ordinary_tool_result_gains_no_agent_fields(manager):
    session = make_session(manager)
    seen = emitted(session)
    session._handle_message(agent_result_message(None, "toolu_bash"))
    assert seen == [("tool_result", {"tool_use_id": "toolu_bash",
                                     "is_error": False, "content": "done"})]


def test_a_result_record_with_no_agent_id_is_not_a_subagent(manager):
    """Every tool's result lands in `tool_use_result`, not just the Agent
    tool's. The id is what says which one this was."""
    session = make_session(manager)
    seen = emitted(session)
    session._handle_message(agent_result_message({"filePath": "/tmp/x",
                                                  "numLines": 12}, "toolu_read"))
    assert "agent_id" not in seen[0][1]


# ------------------------------------- the subagents a limit left with no result


def agent_call_message(tool_use_id="toolu_agent1", description="Find the leak"):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name="Agent",
                              input={"description": description,
                                     "prompt": "look for it"})],
        model="claude-opus-5",
    )


def test_an_agent_call_that_never_reported_back_stays_open(manager):
    session = make_session(manager)
    emitted(session)
    session._handle_message(agent_call_message())
    assert session._open_agents == {"toolu_agent1": "Find the leak"}


def test_an_agent_call_that_reported_back_is_closed(manager):
    session = make_session(manager)
    emitted(session)
    session._handle_message(agent_call_message())
    session._handle_message(agent_result_message(
        {"agentId": "a1"}, "toolu_agent1"))
    assert session._open_agents == {}


def test_an_ordinary_tool_call_is_not_tracked_as_an_agent(manager):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    session = make_session(manager)
    emitted(session)
    session._handle_message(AssistantMessage(
        content=[ToolUseBlock(id="toolu_bash", name="Bash",
                              input={"command": "ls"})],
        model="claude-opus-5"))
    assert session._open_agents == {}


def test_a_blocked_turn_names_the_subagents_it_stranded(manager, monkeypatch):
    """Their ids are not in the transcript -- the calls produced no result --
    so they come off the CLI's own records on disk, matched by tool call."""
    session = make_session(manager)
    manager.get("s1").sdk_session_id = "sess-uuid"
    monkeypatch.setattr(sdk_session.claude_history, "list_subagents",
                        lambda sid: [
                            {"agent_id": "a9", "tool_use_id": "toolu_agent1",
                             "description": "Find the leak"},
                            {"agent_id": "a8", "tool_use_id": "toolu_other",
                             "description": "Something else"},
                        ])
    session._handle_message(agent_call_message())
    session._note_limit_block(FakeRateLimitInfo())
    seen = emitted(session)
    session._report_limit_block()
    assert seen[0][1]["subagents"] == [{"agent_id": "a9",
                                        "description": "Find the leak"}]


def test_a_call_with_no_record_on_disk_is_left_out(manager, monkeypatch):
    """An agent that never got far enough to be filed has no id to offer, and a
    card naming it without one would be an offer that cannot be taken."""
    session = make_session(manager)
    manager.get("s1").sdk_session_id = "sess-uuid"
    monkeypatch.setattr(sdk_session.claude_history, "list_subagents",
                        lambda sid: [])
    session._handle_message(agent_call_message())
    session._note_limit_block(FakeRateLimitInfo())
    seen = emitted(session)
    session._report_limit_block()
    assert seen[0][1]["subagents"] == []


def test_the_stranded_list_is_reported_once(manager, monkeypatch):
    """The calls never return, so without clearing they would be named again at
    the end of every later turn."""
    session = make_session(manager)
    manager.get("s1").sdk_session_id = "sess-uuid"
    monkeypatch.setattr(sdk_session.claude_history, "list_subagents",
                        lambda sid: [{"agent_id": "a9",
                                      "tool_use_id": "toolu_agent1"}])
    session._handle_message(agent_call_message())
    session._note_limit_block(FakeRateLimitInfo())
    emitted(session)
    session._report_limit_block()
    assert session._open_agents == {}


def test_unreadable_records_cost_the_card_its_list_but_not_the_card(manager,
                                                                    monkeypatch):
    def boom(sid):
        raise OSError("gone")

    session = make_session(manager)
    manager.get("s1").sdk_session_id = "sess-uuid"
    monkeypatch.setattr(sdk_session.claude_history, "list_subagents", boom)
    session._handle_message(agent_call_message())
    session._note_limit_block(FakeRateLimitInfo())
    seen = emitted(session)
    assert session._report_limit_block() is True
    assert seen[0][1]["subagents"] == []


# ------------------------------------------------- reading the context window

class FakeContextClient:
    """Answers `get_context_usage` with whatever it was handed, and counts how
    many times it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asks = 0

    async def get_context_usage(self):
        self.asks += 1
        return self.answers[min(self.asks - 1, len(self.answers) - 1)]


def usage(percentage, total=50_000, auto=True, threshold=967_000):
    return {"totalTokens": total, "maxTokens": 1_000_000,
            "percentage": percentage, "isAutoCompactEnabled": auto,
            "autoCompactThreshold": threshold}


def context_session(manager, *answers):
    session = make_session(manager)
    session._loop = asyncio.get_running_loop()
    session._client = FakeContextClient(*answers)
    return session


def readings(manager):
    return [e for e in manager.get_events("s1") if e["type"] == "context_usage"]


@async_test
async def test_a_reading_is_taken_during_a_turn_not_only_at_the_end_of_one():
    # The whole of ISSUE-069: a turn long enough to compact in the middle of
    # itself used to reach its end before anyone heard a figure.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(2))

    await session._read_context_usage()

    assert session._client.asks == 1
    assert readings(manager)[0]["percentage"] == 2


@async_test
async def test_a_second_reading_inside_the_interval_does_not_ask_again():
    # A turn can emit hundreds of messages in a second and the loop offers a
    # reading on each one.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(2))

    for _ in range(50):
        await session._read_context_usage()

    assert session._client.asks == 1


@async_test
async def test_the_end_of_a_turn_reads_however_recent_the_last_one_was():
    # It is the figure the session rests at until someone types again.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(2), usage(9))

    await session._read_context_usage()
    await session._read_context_usage(force=True)

    assert session._client.asks == 2
    assert [r["percentage"] for r in readings(manager)] == [2, 9]


@async_test
async def test_a_reading_that_would_draw_the_same_footer_is_not_stored():
    # Every event is kept for the life of the session and re-sent to every
    # client that subscribes, so an hours-long turn must not leave one every
    # fifteen seconds.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(2, total=50_000),
                              usage(2, total=53_000))

    await session._read_context_usage(force=True)
    await session._read_context_usage(force=True)

    assert session._client.asks == 2
    assert len(readings(manager)) == 1


@async_test
async def test_a_reading_that_moves_the_percentage_is_stored():
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(2), usage(3))

    await session._read_context_usage(force=True)
    await session._read_context_usage(force=True)

    assert [r["percentage"] for r in readings(manager)] == [2, 3]


def test_the_display_key_ignores_a_token_count_and_notices_a_rounding():
    key = sdk_session._context_display_key
    assert key({"percentage": 2.1, "max_tokens": 1}) == key({"percentage": 2.4,
                                                             "max_tokens": 1})
    assert key({"percentage": 2.4, "max_tokens": 1}) != key({"percentage": 2.6,
                                                             "max_tokens": 1})
    # A model change moves the window without moving the percentage.
    assert key({"percentage": 2, "max_tokens": 1_000_000}) != key(
        {"percentage": 2, "max_tokens": 200_000})


@async_test
async def test_a_compaction_boundary_lets_the_next_reading_through():
    # Clients clear the footer on a boundary, so a reading suppressed as a
    # repeat would leave it empty for as long as the percentage held. Driven
    # through the real message handler rather than by clearing the two fields,
    # which would pass whether or not the boundary clears them.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(2))

    await session._read_context_usage(force=True)
    session._handle_message(SystemMessage(subtype="compact_boundary", data={}))
    await session._read_context_usage()

    assert len(readings(manager)) == 2


@async_test
async def test_a_compaction_boundary_makes_the_session_askable_again():
    # Once per cycle rather than once per turn over the line (ISSUE-065).
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(2))
    session._flush_asked = True

    session._handle_message(SystemMessage(subtype="compact_boundary", data={}))

    assert session._flush_asked is False


@async_test
async def test_the_flush_prompt_can_now_fire_in_the_middle_of_a_turn(monkeypatch):
    # The reason ISSUE-069 is a bug rather than a display nicety. The prompt
    # used to get its only chance on the result that ends a turn, so a turn
    # that crossed the threshold in the middle of itself was compacted without
    # ever being asked.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(97))
    monkeypatch.setattr(sdk_session.config_module, "load",
                        lambda: {"context_flush_inject": True,
                                 "context_flush_percent": 90})
    sent = []
    monkeypatch.setattr(session, "send",
                        lambda text, source="operator": sent.append(source) or True)

    await session._read_context_usage()

    assert sent == ["injected"]
    assert session._flush_asked is True


@async_test
async def test_a_suppressed_reading_is_still_put_to_the_flush_check(monkeypatch):
    # What the check decides is whether to interrupt a turn about to lose its
    # context, which does not depend on the footer having changed. The two
    # readings here round to the same percentage, so only the first is stored.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(97, total=970_000),
                              usage(97, total=971_000))
    asked = []
    monkeypatch.setattr(session, "_maybe_ask_for_a_flush", asked.append)

    await session._read_context_usage(force=True)
    await session._read_context_usage(force=True)

    assert len(readings(manager)) == 1
    assert len(asked) == 2


# --------------------------------- the reading runs beside the loop (ISSUE-073)

class SlowContextClient(FakeContextClient):
    """A CLI that cannot answer until something else drains the stream.

    Stands in for the real deadlock: `get_context_usage` waits on `released`,
    which only the message loop can set, so anything that waits for the answer
    instead of reading messages waits forever.
    """

    def __init__(self, *answers):
        super().__init__(*answers)
        self.released = asyncio.Event()
        self.entered = asyncio.Event()

    async def get_context_usage(self):
        self.entered.set()
        await self.released.wait()
        return await super().get_context_usage()


@async_test
async def test_scheduling_a_reading_does_not_wait_for_the_answer():
    # The regression. Before ISSUE-073 this was awaited in the message loop, so
    # a CLI that had not answered yet stopped the loop from reading the
    # messages its answer was queued behind.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = make_session(manager)
    session._loop = asyncio.get_running_loop()
    client = SlowContextClient(usage(2))
    session._client = client

    session._schedule_context_read()          # must return, not block
    await asyncio.wait_for(client.entered.wait(), timeout=1)
    assert readings(manager) == []            # nothing emitted yet
    client.released.set()
    await asyncio.wait_for(session._context_task, timeout=1)
    assert len(readings(manager)) == 1


@async_test
async def test_only_one_reading_is_in_flight_at_a_time():
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = make_session(manager)
    session._loop = asyncio.get_running_loop()
    client = SlowContextClient(usage(2))
    session._client = client

    for _ in range(5):
        session._schedule_context_read()
    await asyncio.wait_for(client.entered.wait(), timeout=1)
    client.released.set()
    await asyncio.wait_for(session._context_task, timeout=1)
    assert client.asks == 1


@async_test
async def test_a_turn_boundary_during_a_reading_is_not_lost():
    # A result that lands while a mid-turn reading is still out is the figure
    # the session rests at, so it gets its own pass rather than being dropped.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = make_session(manager)
    session._loop = asyncio.get_running_loop()
    client = SlowContextClient(usage(2), usage(9))
    session._client = client

    session._schedule_context_read()
    await asyncio.wait_for(client.entered.wait(), timeout=1)
    session._schedule_context_read(force=True)
    client.released.set()
    await asyncio.wait_for(session._context_task, timeout=1)
    assert client.asks == 2
    assert [r["percentage"] for r in readings(manager)] == [2, 9]


@async_test
async def test_a_closed_session_schedules_nothing():
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = context_session(manager, usage(2))
    session._closed = True
    session._schedule_context_read(force=True)
    assert session._context_task is None
    assert readings(manager) == []


class StreamingContextClient(SlowContextClient):
    """Hands the loop a fixed run of messages, and holds every context answer
    until `released` is set. A loop that waits for those answers never reaches
    the end of the run."""

    def __init__(self, messages, *answers):
        super().__init__(*answers)
        self._messages = list(messages)
        self.delivered = 0

    async def receive_messages(self):
        for m in self._messages:
            self.delivered += 1
            yield m
        await asyncio.Event().wait()   # the stream stays open, as the real one does


@async_test
async def test_the_message_loop_does_not_wait_on_the_context_read():
    # ISSUE-073 end to end: a context request that has not been answered must
    # not hold up the messages behind it. Ten messages, none of the readings
    # answered, and the loop still has to get through all ten.
    manager = SessionManager()
    manager.add(Session(id="s1", kind="sdk"))
    session = make_session(manager)
    session._loop = asyncio.get_running_loop()
    msgs = [SystemMessage(subtype="status", data={"status": "requesting"})
            for _ in range(10)]
    client = StreamingContextClient(msgs, usage(2))
    session._client = client

    task = asyncio.create_task(session._read())
    try:
        for _ in range(200):                       # let the loop run
            await asyncio.sleep(0)
            if client.delivered == 10:
                break
        assert client.delivered == 10, (
            f"loop stalled after {client.delivered} of 10 messages")
        # The reading is a task of its own, so give it its turn before asking
        # whether it started; the point is that the loop did not wait for it.
        await asyncio.wait_for(client.entered.wait(), timeout=1)
        assert readings(manager) == []             # asked, still unanswered
    finally:
        client.released.set()
        task.cancel()
