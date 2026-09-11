"""The mapping from stored settings to ClaudeAgentOptions keyword arguments.

The rule the module is built on: a setting the user never chose does not appear
in the kwargs at all, so "default" keeps meaning the CLI's default rather than
a value of ours that happens to match it today. Most of these tests are about
what is *absent* from the dict.
"""

from __future__ import annotations

import pytest

import agent_options


def test_an_empty_config_sends_only_the_system_prompt_preset():
    # The preset is the exception: system_prompt=None makes the SDK pass
    # --system-prompt "", which is how sessions lost Claude Code's own prompt.
    assert agent_options.options_kwargs({}) == {
        "system_prompt": {"type": "preset", "preset": "claude_code"},
        "include_partial_messages": True,
        "forward_subagent_text": True,
    }


def test_turning_the_preset_off_leaves_system_prompt_unset():
    kwargs = agent_options.options_kwargs({"system_prompt_preset": False})
    assert "system_prompt" not in kwargs


def test_appended_text_rides_along_with_the_preset():
    kwargs = agent_options.options_kwargs({"system_prompt_append": "  Be terse.  "})
    assert kwargs["system_prompt"] == {"type": "preset", "preset": "claude_code",
                                       "append": "Be terse."}


def test_an_empty_append_adds_no_key():
    assert "append" not in agent_options.options_kwargs({"system_prompt_append": "   "})["system_prompt"]


def test_a_model_is_sent_only_when_one_is_named():
    assert "model" not in agent_options.options_kwargs({"model": "  "})
    assert agent_options.options_kwargs({"model": " claude-opus-5 "})["model"] == "claude-opus-5"


def test_a_non_string_model_is_ignored_rather_than_passed_on():
    assert agent_options.model({"model": None}) == ""
    assert agent_options.model({"model": 42}) == ""


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max"])
def test_a_chosen_effort_is_sent(level):
    assert agent_options.options_kwargs({"effort": level})["effort"] == level


def test_the_default_effort_is_not_sent():
    assert "effort" not in agent_options.options_kwargs({"effort": agent_options.UNSET})


def test_an_unknown_effort_falls_back_to_the_default():
    assert agent_options.effort({"effort": "turbo"}) == agent_options.UNSET
    assert "effort" not in agent_options.options_kwargs({"effort": "turbo"})


def test_disabled_thinking_is_sent_as_its_own_shape():
    assert agent_options.options_kwargs({"thinking": "disabled"})["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("mode", ["summarized", "omitted"])
def test_an_on_thinking_mode_is_sent_as_adaptive_with_a_display(mode):
    assert agent_options.options_kwargs({"thinking": mode})["thinking"] == {
        "type": "adaptive", "display": mode}


def test_the_default_thinking_mode_is_not_sent():
    assert "thinking" not in agent_options.options_kwargs({})


def test_streaming_off_leaves_include_partial_messages_unset():
    assert "include_partial_messages" not in agent_options.options_kwargs(
        {"stream_partial_text": False})


def test_subagent_text_off_leaves_the_key_unset():
    assert "forward_subagent_text" not in agent_options.options_kwargs(
        {"forward_subagent_text": False})


def test_mcp_keys_are_never_set():
    # strict_mcp_config=True would silently drop every server the user
    # configured outside Codinian; leaving both unset is the feature.
    kwargs = agent_options.options_kwargs({"model": "x", "effort": "high",
                                           "thinking": "summarized"})
    assert "mcp_servers" not in kwargs
    assert "strict_mcp_config" not in kwargs


# ------------------------------------------------------------- conflicts

@pytest.mark.parametrize("level", ["xhigh", "max"])
def test_disabled_thinking_above_high_effort_is_reported_as_a_conflict(level):
    warning = agent_options.conflicts({"thinking": "disabled", "effort": level})
    assert warning is not None
    assert "xhigh" in warning


@pytest.mark.parametrize("level", ["low", "medium", "high", agent_options.UNSET])
def test_disabled_thinking_at_or_below_high_effort_is_fine(level):
    assert agent_options.conflicts({"thinking": "disabled", "effort": level}) is None


def test_no_conflict_when_thinking_is_left_on():
    assert agent_options.conflicts({"thinking": "summarized", "effort": "max"}) is None


# -------------------------------------------------------------- booleans

@pytest.mark.parametrize("stored, expected", [
    (True, True), (False, False), (None, True), ("anything", True), (0, True),
])
def test_a_boolean_setting_is_off_only_when_it_is_exactly_false(stored, expected):
    # `is not False` rather than a truth test: a setting written by an older
    # version, or missing, means "on" here.
    assert agent_options.forward_subagent_text({"forward_subagent_text": stored}) is expected
    assert agent_options.stream_partial_text({"stream_partial_text": stored}) is expected
    assert agent_options.use_preset({"system_prompt_preset": stored}) is expected


# ------------------------------------------------------- permission mode

@pytest.mark.parametrize("mode", agent_options.PERMISSION_MODES)
def test_every_permission_mode_survives(mode):
    assert agent_options.default_permission_mode({"default_permission_mode": mode}) == mode


def test_an_unknown_permission_mode_falls_back_to_default():
    assert agent_options.default_permission_mode({"default_permission_mode": "yolo"}) == "default"
    assert agent_options.default_permission_mode({}) == "default"


def test_the_permission_modes_here_match_the_ones_the_session_layer_accepts():
    import session
    assert set(agent_options.PERMISSION_MODES) == set(session.PERMISSION_MODES)


# --------------------------------------------------------- display prefs

def test_display_prefs_default_to_plan_usage_only():
    # A subscription's constraint is the plan's windows, not dollars.
    assert agent_options.display_prefs({}) == {"show_plan_usage": True,
                                               "show_cost": False,
                                               "show_cache_tokens": False}


def test_display_prefs_coerce_to_booleans():
    prefs = agent_options.display_prefs({"show_cost": 1, "show_plan_usage": ""})
    assert prefs["show_cost"] is True
    assert prefs["show_plan_usage"] is False
