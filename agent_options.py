"""How Codinian talks to Claude Code (ISSUE-032).

`ClaudeAgentOptions` has a dozen knobs that shape every session, and until this
issue Codinian set exactly three of them: the working directory, the permission
mode and the approval hook. Everything else took the SDK's default, which is not
always the CLI's default and in one case is actively surprising:

    # claude_agent_sdk/_internal/transport/subprocess_cli.py
    if self._options.system_prompt is None:
        cmd.extend(["--system-prompt", ""])

`system_prompt=None` does not mean "leave it alone", it means "run with no system
prompt at all". Sessions were losing Claude Code's own prompt, which is why a
transcript read as a list of tool calls with almost no narration between them.

This module owns those settings: it reads them from the global config, and turns
them into keyword arguments for `ClaudeAgentOptions`. Kept free of `gi` imports
so the mapping can be checked without a display, and free of SDK imports so it
can be checked without a working SDK install.
"""

from __future__ import annotations

# Unset everywhere below means "send nothing and let the CLI decide". That is
# the value Codinian shipped with, so an existing config keeps its behaviour.
UNSET = "default"

# `output_config.effort` on the request. The single largest lever on cost,
# latency and answer quality; the CLI's own default is high.
EFFORT_LEVELS = (UNSET, "low", "medium", "high", "xhigh", "max")

# What the model does with its reasoning, and what we see of it.
#
#   summarized  a readable summary of the reasoning comes back
#   omitted     thinking still happens and is still billed, but the text is
#               empty. The default on current models, and the reason the
#               transcript's Thinking cards expand to nothing today.
#   disabled    no extended thinking at all
THINKING_MODES = (UNSET, "summarized", "omitted", "disabled")

# Effort levels that current models refuse to pair with disabled thinking.
# Claude Opus 5 returns a 400 for the combination rather than clamping it.
EFFORT_REJECTS_DISABLED_THINKING = frozenset({"xhigh", "max"})

PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions",
                    "dontAsk", "auto")

DEFAULTS = {
    "model": "",
    "forward_subagent_text": True,
    "stream_partial_text": True,
    "effort": UNSET,
    "thinking": UNSET,
    "system_prompt_preset": True,
    "system_prompt_append": "",
    "default_permission_mode": "default",
    # What the strip along the bottom of a transcript shows. Plan usage is on
    # and cost is off by default because this app signs in with a subscription:
    # Anthropic's own docs say the session cost figure "isn't relevant for
    # billing purposes" for Pro and Max subscribers, where the constraint is the
    # plan's windows rather than dollars. An API-key user turns cost back on.
    "show_plan_usage": True,
    "show_cost": False,
    # Cache creation and cache read counts. Off by default: they are the two
    # widest numbers in the footer and the least often acted on -- prompt
    # caching is automatic, so the figures describe something nobody is
    # deciding about.
    "show_cache_tokens": False,
}

# The subset of the above the transcript page needs; it crosses to the browser
# as a unit, so it is named in one place.
DISPLAY_KEYS = ("show_plan_usage", "show_cost", "show_cache_tokens")


def _choice(config: dict, key: str, allowed) -> str:
    value = config.get(key, DEFAULTS[key])
    return value if value in allowed else DEFAULTS[key]


def display_prefs(config: dict) -> dict:
    """Which of the footer's two optional parts to show."""
    return {key: bool(config.get(key, DEFAULTS[key])) for key in DISPLAY_KEYS}


def model(config: dict) -> str:
    """The model id to run sessions on, or "" to let the CLI choose.

    Free text rather than a dropdown on purpose: model ids change faster than
    this app ships, and a hardcoded list would be wrong within months. A typo
    fails at session start with the API's own message, which is a clearer
    error than a stale option would be."""
    value = config.get("model", "")
    return value.strip() if isinstance(value, str) else ""


def effort(config: dict) -> str:
    return _choice(config, "effort", EFFORT_LEVELS)


def thinking(config: dict) -> str:
    return _choice(config, "thinking", THINKING_MODES)


def use_preset(config: dict) -> bool:
    """Whether to run with Claude Code's own system prompt.

    On by default. A session without it has no system prompt whatsoever, which
    is the ISSUE-032 bug rather than a setting anyone would choose."""
    return config.get("system_prompt_preset", True) is not False


def system_prompt_append(config: dict) -> str:
    value = config.get("system_prompt_append", "")
    return value.strip() if isinstance(value, str) else ""


def forward_subagent_text(config: dict) -> bool:
    """Whether a subagent's prose and reasoning reach the transcript.

    On by default. The SDK forwards a subagent's tool_use and tool_result
    blocks either way, so this is the difference between watching a subagent
    work and reading only what it decided to say at the end (ISSUE-017)."""
    return config.get("forward_subagent_text", True) is not False


def stream_partial_text(config: dict) -> bool:
    """Whether assistant text appears as it is written (ISSUE-033).

    On by default. Only text is forwarded; thinking and tool-argument deltas
    are 99% of the volume and neither is legible as it arrives."""
    return config.get("stream_partial_text", True) is not False


def default_permission_mode(config: dict) -> str:
    """The fallback mode for a new session in a folder that is not a
    registered project. A project's own .codinian/settings.json wins."""
    return _choice(config, "default_permission_mode", PERMISSION_MODES)


def conflicts(config: dict) -> str | None:
    """The one settings combination current models reject, or None.

    Surfaced in the UI rather than left to fail at session start: a 400 from
    the API names the parameter but not which of two controls to change."""
    if thinking(config) == "disabled" and effort(config) in EFFORT_REJECTS_DISABLED_THINKING:
        return ("Disabling thinking is only accepted up to high effort. Current "
                "models reject it at xhigh and max, so sessions would fail to "
                "start.")
    return None


def options_kwargs(config: dict) -> dict:
    """Keyword arguments for `ClaudeAgentOptions`, from the stored settings.

    Only settings the user has actually chosen appear: a key left out is a key
    the SDK never sees, which keeps "default" meaning the CLI's default rather
    than a value of ours that happens to match it today.
    """
    kwargs: dict = {}

    if use_preset(config):
        preset: dict = {"type": "preset", "preset": "claude_code"}
        append = system_prompt_append(config)
        if append:
            preset["append"] = append
        kwargs["system_prompt"] = preset
    # Left unset otherwise, which the SDK sends as `--system-prompt ""`. That
    # is the pre-ISSUE-032 behaviour and now only happens by choice.

    name = model(config)
    if name:
        kwargs["model"] = name

    level = effort(config)
    if level != UNSET:
        kwargs["effort"] = level

    if stream_partial_text(config):
        kwargs["include_partial_messages"] = True
    # Left unset otherwise: the SDK's default is False, and the deltas are
    # simply never emitted, so the renderer has nothing to ignore.

    if forward_subagent_text(config):
        kwargs["forward_subagent_text"] = True
    # Left unset otherwise: the SDK's own default is False, and sending it
    # explicitly would only differ if that default ever changed.

    mode = thinking(config)
    if mode == "disabled":
        kwargs["thinking"] = {"type": "disabled"}
    elif mode != UNSET:
        # Adaptive is the only on-mode current models accept; `display` is what
        # decides whether the reasoning comes back as text or as an empty
        # string.
        kwargs["thinking"] = {"type": "adaptive", "display": mode}

    # Deliberately never sets `mcp_servers` or `strict_mcp_config`.
    #
    # ISSUE-014 asked for per-working-directory MCP configuration. The CLI
    # already does it: `strict_mcp_config` defaults to False, which its own
    # docstring defines as loading project `.mcp.json`, user and global
    # settings, and plugin-provided servers. A session Codinian starts in a
    # folder therefore picks up that folder's `.mcp.json` the same way the CLI
    # would.
    #
    # Setting either key is what would break that: `mcp_servers` alone adds to
    # the set, but `strict_mcp_config=True` would silently drop every server
    # the user configured outside Codinian. Leaving both unset is the feature.
    return kwargs
