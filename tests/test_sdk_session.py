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
                                          "prompt": "run the suite"})]


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
