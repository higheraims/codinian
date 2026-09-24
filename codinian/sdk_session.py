"""Drives one Claude Agent SDK session and maps its stream to TranscriptEvents
(ISSUE-004).

One SdkSession owns one ClaudeSDKClient and runs entirely inside the shared
asyncio loop (main.py starts that loop in a dedicated thread alongside the
aiohttp server). It turns each SDK message into a manager event, and gates
every tool call through a PreToolUse hook: the hook emits an approval_request,
suspends on a future, and resolves when any client answers.

Why a PreToolUse hook and not can_use_tool: the hook fires for every tool even
when the user's ~/.claude allow rules would auto-approve, so we keep the
project's CLAUDE.md / skills / settings context loaded (setting_sources stays
default) while still gating each call. This was settled in the M0/M1 spikes
(ISSUE-002).

The cost of that choice is that the hook runs ahead of the CLI's own permission
handling and its answer is final, so a hook that always asks makes every
permission mode behave like `default` -- which is what ISSUE-027 reported. The
hook therefore reads the session's current mode before it decides: see
`_pre_tool_use` and MODE_DECIDES_ITSELF below.

Known hazard (ISSUE-008): the hook blocks the turn until it is answered. With
no client attached, or after the app restarts, an unanswered approval leaves
the session parked. M1 documents this; ISSUE-008 handles staleness.

The wait is not unbounded, and what happens at the end of it is the subject of
ISSUE-052: the CLI abandons a hook after HOOK_TIMEOUT_SECONDS and cancels the
callback, which `_wait_for_answer` turns into an `approval_expired` event so no
client is left showing a card that can no longer be answered.
"""

from __future__ import annotations

import asyncio
import threading
import traceback
import uuid

from claude_agent_sdk import (
    AssistantMessage,
    StreamEvent,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from . import agent_options
from . import claude_cli
from . import claude_history
from . import config as config_module
from . import images
from . import plan_usage
from .events import SessionStatus
from .session import SessionManager


# How long a session creation waits for the asyncio loop to come up before it
# gives up and reports the session as failed.
LOOP_START_TIMEOUT = 10.0

# How long a quit waits for every live session to disconnect (ISSUE-037).
# Long enough for a `claude` subprocess to be told and go, short enough that a
# stuck one does not read as an app that will not close.
SHUTDOWN_TIMEOUT = 5.0

# How hard to look for the title the CLI generates for a conversation, after a
# turn ends. It is written a beat behind the turn it summarises, and a session
# left alone after one question has no next turn to try again on.
NAME_LOOKUP_ATTEMPTS = 3
NAME_LOOKUP_DELAY = 4.0


# Permission modes whose whole point is that no human is asked, and whose answer
# lives in the CLI rather than here: `auto` runs a model classifier over each
# call, `dontAsk` applies the settings files' allow rules and denies the rest.
# Neither can be reproduced in the hook, so for these the hook returns no
# decision and lets the call fall through to the CLI (ISSUE-027).
#
# `plan` is here for a different reason (ISSUE-049). The CLI enforces plan mode
# itself, and it enforces it harder than this hook can: it refuses every edit
# without consulting the client at all, and it auto-approves read-only Bash. A
# hook that asks about each call therefore added nothing but a card per `cat`
# and per `grep`, which is what made a planning session unusable. Measured on
# three read-only commands: three cards before, none after, and a `Write` in
# the same session still went nowhere but the plan file.
MODE_DECIDES_ITSELF = frozenset({"auto", "dontAsk", "plan"})

# The one call plan mode must still put to the user. Deferring it would hand the
# plan to the CLI expecting a client that approves plans, and Codinian is not
# one, so the turn would park on a question nobody is shown (the ISSUE-008
# hazard). Leaving a plan for review is a decision, not a tool call.
ALWAYS_ASK = frozenset({"ExitPlanMode"})

# The tool that asks the user a question rather than doing anything (ISSUE-050).
#
# The CLI only registers it when the client supplies a `can_use_tool` callback,
# which is why it was missing from every Codinian session: this app gates on a
# PreToolUse hook and passed no callback. Measured as a 2x2 -- callback alone
# yes, hook alone no, both yes, neither no.
#
# It is answered rather than approved. The permission callback collects the
# answer, and a PostToolUse hook swaps it into the tool's output, so the model
# reads an ordinary successful result instead of a denial.
QUESTION_TOOL = "AskUserQuestion"

# The tool that spawns a subagent. Named here because a call to it is the only
# one whose id has to outlive the turn: a subagent a usage limit killed is
# continued by an id that the call it belongs to never got to report
# (ISSUE-059).
AGENT_TOOL = "Agent"

# How long deltas accumulate before one frame goes out (ISSUE-033).
#
# Measured on the wire: the event envelope is 142 bytes around a 4-byte token,
# so one frame per token spends 270 bytes to carry 4 and puts 50 packets a
# second on a phone's radio. Flushing on a timer instead collapses both. At
# 100 ms and typical generation speed that is ~5 tokens a frame, a fifth of the
# bytes and a fifth of the packets, and still fast enough to read as live.
DELTA_FLUSH_SECONDS = 0.1

# Tools `acceptEdits` covers. Anything outside this set is still put to the user
# in that mode, which is the difference between it and `bypassPermissions`.
EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})

# How long a PreToolUse hook may take before the CLI abandons it (ISSUE-052).
#
# The CLI's own default is 600 seconds, measured exactly: a hook that slept for
# 600 was cancelled 600.0 seconds after it was entered, and the model was told
# "PreToolUse hook did not respond before its timeout (host client may be
# unreachable)". Every approval waits inside that hook, so the default put a
# ten-minute ceiling on how long a person had to answer one. The field is in
# seconds and replaces the default rather than capping it.
#
# A day, because the shortest honest answer to "how long may an approval sit"
# is "until the user gets back", and an approval raised at the end of an evening
# is a normal case rather than an error. It is not unbounded: a session parked
# on an approval nobody will ever answer should eventually stop holding the
# turn, and this is the value at which that happens.
HOOK_TIMEOUT_SECONDS = 24 * 60 * 60

# How long a reading of the context window stays good enough to skip taking
# another (ISSUE-069).
#
# The reading used to be taken once, on the result that ends a turn, so a turn
# long enough to compact in the middle of itself was compacted against a footer
# reporting the figure from before it started, and the flush prompt never fired
# because its only chance to fire was that same result. The session that found
# this ran three and a half hours and 57 tool calls on one turn, with one
# reading taken in its first minute.
#
# So the loop asks as messages arrive instead, and this is what stops it asking
# on every one of them. Fifteen seconds because the number it feeds is read by
# a person, who cannot use it faster than that, and because the gap it leaves
# is the window in which the CLI can cross its threshold unseen: a turn would
# have to add the last few percent of a context window inside fifteen seconds
# to get past it.
#
# A gate on elapsed time rather than a timer task, so an idle session asks
# nothing at all. There is no clock here except messages arriving, and a
# session with no turn in flight has none.
CONTEXT_READ_INTERVAL = 15.0

# How many sent messages to keep the text of, for naming the ones a stop throws
# away. Only messages still in the CLI's queue can be thrown, and that queue is
# what one person can type during one turn, so this is generous rather than
# tuned.
SENT_TEXT_REMEMBERED = 64

# How much of a cancelled message to quote back when naming it.
CANCELLED_QUOTE_CHARS = 60

# States in which the session already has a turn in flight, so a message
# arriving does not change what it is doing. See `_run`.
BUSY_STATUSES = frozenset({
    SessionStatus.WORKING, SessionStatus.AWAITING_APPROVAL,
})

# States in which no turn is running, so content arriving is proof one started
# again. `rate_limited` belongs here with `awaiting_input` because it differs
# from it only in why the session stopped, not in what it is doing (ISSUE-058).
IDLE_STATUSES = frozenset({
    SessionStatus.AWAITING_INPUT, SessionStatus.RATE_LIMITED,
})


def _one_line(text: str) -> str:
    """A message squashed to one short line, for quoting back in a note."""
    flat = " ".join(text.split())
    if len(flat) > CANCELLED_QUOTE_CHARS:
        flat = flat[:CANCELLED_QUOTE_CHARS - 1].rstrip() + "…"
    return f'"{flat}"'


def _context_display_key(event: dict) -> tuple:
    """What a client would draw from a context reading. Two readings with the
    same key put the same footer on the screen, so only the first is kept
    (ISSUE-069)."""
    pct = event.get("percentage")
    return (
        round(pct) if isinstance(pct, (int, float)) else None,
        event.get("auto_compact"),
        event.get("threshold"),
        event.get("max_tokens"),
    )


def _model_usage_totals(model_usage) -> dict | None:
    """Cumulative token totals for a session, summed across every model it used.

    `ResultMessage.usage` describes only the last API call and comes back all
    zeros often enough to be useless as a running total. `model_usage` is a
    per-model breakdown that accumulates over the conversation, and it counts
    the models the SDK uses on the side (a haiku call for the session title)
    as well as the one doing the work. Measured across three turns in one
    session: model_usage rose 899 to 901 to 903 input tokens while `usage`
    reported zero for the middle turn."""
    if not isinstance(model_usage, dict) or not model_usage:
        return None
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    for entry in model_usage.values():
        if not isinstance(entry, dict):
            continue
        totals["input"] += entry.get("inputTokens", 0) or 0
        totals["output"] += entry.get("outputTokens", 0) or 0
        totals["cache_read"] += entry.get("cacheReadInputTokens", 0) or 0
        totals["cache_creation"] += entry.get("cacheCreationInputTokens", 0) or 0
    return totals


def _report_start_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)


class SdkSession:
    def __init__(self, manager: SessionManager, session_id: str, workdir: str,
                 permission_mode: str = "default", resume: str | None = None):
        self._manager = manager
        self._session_id = session_id
        self._workdir = workdir
        self._permission_mode = permission_mode
        self._resume = resume

        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: ClaudeSDKClient | None = None
        self._run_task: asyncio.Task | None = None
        # The reader, which runs for the session's life rather than for a turn
        # (ISSUE-054).
        self._read_task: asyncio.Task | None = None
        # (uuid, text) pairs, or None to stop the loop. The uuid is ours to
        # choose and is what makes a message cancellable after it has been
        # sent; see `send` and `interrupt`.
        self._sends: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        # What each message we have handed over said, so a cancellation can
        # name it rather than count it. Bounded, because nothing tells us when
        # a message has been consumed: the CLI's command_lifecycle events are
        # internal to it and the SDK does not surface them.
        self._sent_text: dict[str, str] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._pending_meta: dict[str, dict] = {}
        self._closed = False
        # Set when the turn loop has stopped on an error and will not run
        # again. `send` reads it to refuse rather than queue onto a queue with
        # no consumer (ISSUE-036).
        self._failed = False
        # Streaming deltas, accumulated per content-block index and flushed on a
        # timer rather than per token (ISSUE-033).
        self._delta_buf: dict[int, list[str]] = {}
        self._delta_task: asyncio.Task | None = None
        # The context reading runs beside the message loop rather than inside
        # it (ISSUE-073). One at a time, with `_context_force` remembering a
        # turn boundary that arrived while one was already running.
        self._context_task: asyncio.Task | None = None
        self._context_force = False
        # Filled at connect; see start(). Empty when the CLI did not answer.
        self._server_info: dict = {}
        # Protocol capabilities the CLI advertises, from the init message
        # rather than from server_info, which does not carry them. None until
        # the first turn brings init with it; see `_handle_message`.
        self._capabilities: list[str] | None = None
        # The running look for this conversation's generated title, if any.
        self._name_task: asyncio.Task | None = None
        # AskUserQuestion answers, keyed by tool_use_id, waiting for the
        # PostToolUse hook to put them in the tool's output (ISSUE-050).
        self._answers: dict[str, dict] = {}
        # The last status emitted, so the reader can tell content arriving
        # while the session shows idle from ordinary mid-turn traffic
        # (ISSUE-055).
        self._status: SessionStatus | None = None
        # A usage limit that closed the window this turn was going to run in,
        # held until the turn ends so the card can say what it cost (ISSUE-058).
        # None whenever the model is producing, which is proof it did not.
        self._limit_block: dict | None = None
        # The prompt handed to the CLI most recently, which is what a blocked
        # turn was going to be about.
        self._last_query_text: str | None = None
        # `Agent` calls that have not reported back: tool_use_id -> description.
        # A subagent a limit cut off is one of these and stays one, because the
        # call it belongs to never returns (ISSUE-059).
        self._open_agents: dict[str, str] = {}
        # Whether this session has already been asked to save its reasoning
        # before the window fills. Cleared by a compaction boundary, so a long
        # session gets asked once per cycle rather than once per turn over the
        # threshold (ISSUE-065).
        self._flush_asked = False
        # When the context window was last measured, on the event loop's clock.
        # Zero rather than None so the first message of the first turn asks
        # (ISSUE-069).
        self._context_read_at = 0.0
        # The last reading a client was actually shown, so readings that would
        # draw the same footer are not stored and re-sent.
        self._last_context_shown: tuple | None = None

    # ------------------------------------------------------------- lifecycle

    async def start(self, initial_prompt: str | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        config = config_module.load()
        options = ClaudeAgentOptions(
            cwd=self._workdir,
            permission_mode=self._permission_mode,
            hooks={
                # The timeout is the whole reason this matcher is spelled out:
                # approvals wait inside the hook, and the CLI's default gave
                # them ten minutes (ISSUE-052).
                "PreToolUse": [HookMatcher(hooks=[self._pre_tool_use],
                                           timeout=HOOK_TIMEOUT_SECONDS)],
                # Only there to hand AskUserQuestion its answer; see
                # `_post_tool_use`, which ignores every other tool.
                "PostToolUse": [HookMatcher(hooks=[self._post_tool_use])],
            },
            # Supplying this is what makes AskUserQuestion exist at all
            # (ISSUE-050). It also picks up the calls the CLI decides to ask
            # about in the modes where the hook defers, which previously had
            # nowhere to go.
            can_use_tool=self._can_use_tool,
            resume=self._resume,
            # System prompt, model, effort and thinking visibility, from the
            # user's settings (ISSUE-032). Read at start rather than held on
            # the session, so a change applies to the next session without a
            # restart. Only settings actually chosen appear in the mapping.
            **agent_options.options_kwargs(config),
            # Which `claude` to run (ISSUE-060). Absent when nothing was found,
            # which leaves the SDK to search and to raise its own not-found
            # error, the one that names the installer for this platform.
            **claude_cli.options_kwargs(config),
        )
        self._emit_status(SessionStatus.INITIALIZING)
        self._client = ClaudeSDKClient(options=options)
        try:
            await self._client.connect()
        except Exception as exc:
            self._client = None
            self._emit("system", {"subtype": "error", "data": {"message": str(exc)}})
            self._emit_status(SessionStatus.ERROR)
            raise
        # What this session can be asked to do (ISSUE-016). One control-protocol
        # round trip at connect: the CLI answers with every slash command and
        # skill it has, each with a description and an argument hint, which is
        # the affordance a GUI has to build for itself because there is no `/`
        # to type into. Cached because it does not change for the session's
        # life, and a failure here must not stop the session starting.
        try:
            info = await self._client.get_server_info()
            if isinstance(info, dict):
                self._server_info = info
        except Exception:
            pass

        # The reader starts first, so nothing the CLI says can arrive before
        # somebody is listening for it.
        self._read_task = asyncio.create_task(self._read())
        self._run_task = asyncio.create_task(self._run())
        if initial_prompt:
            self.send(initial_prompt)
        else:
            # Connected with nothing to do yet. The SDK stays silent until the
            # first query (its own init message rides on that response), so
            # without this the session would sit at "initializing" forever and
            # look hung.
            self._emit_status(SessionStatus.AWAITING_INPUT)

    async def close(self) -> None:
        self._closed = True
        images.store.forget_session(self._session_id)
        await self._sends.put(None)
        if self._name_task:
            self._name_task.cancel()
        if self._delta_task:
            self._delta_task.cancel()
        if self._context_task:
            self._context_task.cancel()
        if self._run_task:
            self._run_task.cancel()
        if self._read_task:
            self._read_task.cancel()
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass

    # -------------------------------------------------------- turn processing

    async def _run(self) -> None:
        """Hands the CLI each message as it is written. Reading what comes back
        is `_read`'s job, running alongside this one.

        The two used to be one loop, which is what made ISSUE-054 possible: a
        loop that reads only between `query()` and the result leaves a window
        with nobody listening, and the SDK keeps filling it.

        This loop used to wait for the turn to end before handing over the next
        message, on the reasoning that a prompt typed while Claude is working
        belongs to the next turn rather than the middle of this one. That was
        wrong, and it cost the feature the wait was protecting. The CLI keeps a
        command queue of its own: a message written to it mid-turn is absorbed
        by the turn already running, at the next step of the agent loop, so the
        model reads it without the turn having to end first. Measured against a
        turn working through four sequential 15-second shell commands, a
        message sent 20 seconds in was read after the first command returned
        and the remaining three were abandoned. Holding it here meant it
        arrived after all four instead.

        What the wait did guarantee, and this still does, is that one sender
        writes to the CLI at a time and in the order the messages were typed.

        The limit worth knowing: absorption happens between tool calls, not
        during one. A message sent while a command is blocked waits for that
        command to return. Redirecting Claude away from something genuinely
        hung is `interrupt`'s job, not this one's.
        """
        try:
            while not self._closed:
                item = await self._sends.get()
                if item is None:
                    break
                message_uuid, text = item
                # Only when nothing is in flight. A message sent mid-turn does
                # not change what the session is doing, and one sent while an
                # approval is on screen would put the status back to working
                # and hide a card that still needs an answer.
                if self._status not in BUSY_STATUSES:
                    self._emit_status(SessionStatus.WORKING)
                self._last_query_text = text
                await self._client.query(self._envelope(message_uuid, text))
                if self._failed:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface, do not swallow
            self._fail(str(exc))

    async def _read(self) -> None:
        """Everything the CLI says, for as long as the session lives.

        `receive_messages()` rather than `receive_response()`, which the SDK
        documents as terminating on the first `ResultMessage`. That return is
        the bug in ISSUE-054: the SDK's own reader task keeps parsing CLI
        output into a stream whatever we are doing, so anything written after a
        turn's result was read, queued, and then handed over at the top of the
        next turn -- arriving under that turn's prompt echo, looking like an
        instant answer to the wrong question.

        The two kinds of failure are still worth separating. A raise out of
        `_handle_message` is a bug in our own mapping of one block, and killing
        a working conversation over an unrenderable block is the wrong trade,
        so those are reported per message and the session carries on. The pipe
        to the `claude` subprocess going away has nothing left to carry on
        with, so this stops and says so, and `send` refuses from then on rather
        than queueing into the dark (ISSUE-036).
        """
        try:
            async for msg in self._client.receive_messages():
                # Before the content, not after it: a client should see the
                # session go busy and then see what it is busy with.
                if isinstance(msg, (AssistantMessage, UserMessage)):
                    self._resume_if_idle()
                try:
                    self._handle_message(msg)
                except Exception as exc:
                    self._emit("system", {"subtype": "error", "data": {
                        "message": f"could not render one message: {exc}"}})
                if isinstance(msg, ResultMessage):
                    self._end_turn()
                    self._schedule_context_read(force=True)
                else:
                    # Mid-turn, so a turn that fills the window does not reach
                    # its end before anyone hears about it (ISSUE-069). Rate
                    # limited by the clock, not by the message, because a turn
                    # can emit hundreds of these in a second.
                    #
                    # Scheduled, never awaited: the answer comes back down this
                    # same stream, so a loop that waits here is not reading the
                    # messages the answer is queued behind (ISSUE-073).
                    self._schedule_context_read()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface, do not swallow
            self._fail(str(exc))
        else:
            # The stream ended on its own: the CLI is gone, and a session that
            # cannot hear it is over whether or not anything raised.
            if not self._closed:
                self._fail("the connection to the claude process ended")

    def _resume_if_idle(self) -> None:
        """The CLI is producing content while the session says it is idle, so
        it is not idle (ISSUE-055).

        A turn that leaves a background task running ends on a result, and the
        CLI picks the work back up when the task reports. Everything after that
        renders, but the pane went on saying "awaiting input" through all of
        it, because nothing but a prompt ever set the status back.

        Only an assistant or user message counts. A rate limit event or a task
        notification can arrive on a genuinely idle session, and a status that
        went to working on one of those would have nothing to bring it back."""
        if self._closed or self._status not in IDLE_STATUSES:
            return
        self._emit_status(SessionStatus.WORKING)

    def _end_turn(self) -> None:
        """A `ResultMessage` went past, so the turn is over.

        The status change lives here rather than where the reading stops,
        because those were the same place before ISSUE-054 and only one of them
        was right: the turn ends when the CLI says so."""
        blocked = self._report_limit_block()
        self._schedule_name_lookup()
        if not self._closed:
            # A blocked session takes input exactly as an idle one does, and
            # says the opposite thing about why it stopped (ISSUE-058).
            self._emit_status(SessionStatus.RATE_LIMITED if blocked
                              else SessionStatus.AWAITING_INPUT)

    def _report_limit_block(self) -> bool:
        """Say that this turn ended on a usage limit rather than on an answer,
        and hand back the prompt it was going to run (ISSUE-058).

        Without this the two are indistinguishable: both leave the session in
        `awaiting_input` with the last thing the user typed and no reply under
        it. The CLI does wait out the limit and carry on by itself, but only
        for an interactive session -- the check is `isInteractive()`, ahead of
        everything else -- and a session the SDK drives is never that. So the
        window and its reset time are reported here and the resume is left to
        the user, which is also the only version that cannot spend a fresh
        window on a turn nobody is watching."""
        block, self._limit_block = self._limit_block, None
        if block is None:
            return False
        self._emit("rate_limit_block", {
            **block,
            "prompt": self._last_query_text,
            "subagents": self._stranded_subagents(),
        })
        # One limit, one list. These calls never return, so without this they
        # would be reported again at the end of every later turn.
        self._open_agents.clear()
        return True

    def _stranded_subagents(self) -> list[dict]:
        """The subagents that were still running when the limit landed, each
        with the id that continues it (ISSUE-059).

        Their ids are not in the transcript: an agent id reaches the parent on
        the `Agent` call's result, and these calls produced none. What does have
        them is the CLI's own bookkeeping on disk, which pairs an agent id with
        the tool call that started it as soon as the agent exists.

        Reads the disk on the loop thread, which is worth it here: this runs
        once per limit, against a directory holding one small file per subagent.
        """
        session = self._manager.get(self._session_id)
        sdk_session_id = getattr(session, "sdk_session_id", None)
        if not self._open_agents or not sdk_session_id:
            return []
        try:
            records = claude_history.list_subagents(sdk_session_id)
        except Exception:
            # Best effort. A resume offered without the agent list is still a
            # resume; one that raised here would leave no card at all.
            return []
        # Newest first from `list_subagents`, so the first record for a tool
        # call is its most recent attempt.
        by_call: dict = {}
        for record in records:
            by_call.setdefault(record.get("tool_use_id"), record)
        stranded = []
        for tool_use_id, described in self._open_agents.items():
            record = by_call.get(tool_use_id)
            if record is None:
                continue
            stranded.append({
                "agent_id": record["agent_id"],
                "description": record.get("description") or described or "",
            })
        return stranded

    def _fail(self, message: str) -> None:
        """The session has stopped for good. Say so, refuse further sends,
        and account for anything already queued behind the failure rather than
        leaving it to look delivered (ISSUE-036)."""
        self._failed = True
        self._emit("system", {"subtype": "error", "data": {"message": message}})

        orphaned = 0
        while True:
            try:
                queued = self._sends.get_nowait()
            except asyncio.QueueEmpty:
                break
            if queued is not None:
                orphaned += 1
        if orphaned:
            self._emit("system", {"subtype": "error", "data": {"message": (
                f"{orphaned} queued message(s) were not delivered. "
                "Resume this session from History to carry on.")}})
        self._emit_status(SessionStatus.ERROR)

    def _schedule_name_lookup(self) -> None:
        """Look for the conversation's title, off the turn, once per turn."""
        if self._name_task is not None and not self._name_task.done():
            return
        self._name_task = asyncio.create_task(self._adopt_generated_name())

    async def _adopt_generated_name(self) -> None:
        """Take the CLI's own title for this conversation as the session name.

        A session is created named after the folder it runs in, which is
        already on the row beside it, so two sessions in one project read as
        the same session. The CLI writes a title of its own into the transcript
        during the first turn and refines it as the conversation goes on, so
        this runs after every turn rather than once.

        It runs beside the turn rather than in it, because the title is written
        a moment after the turn it summarises: a session that answers one
        question and is then left alone would keep the folder name if this only
        ever looked once, the instant the turn ended. Failure is not worth
        reporting anywhere -- the session keeps the name it has.

        A session renamed by hand keeps that name: see set_auto_name."""
        for attempt in range(NAME_LOOKUP_ATTEMPTS):
            session = self._manager.get(self._session_id)
            if session is None or session.name_is_custom or not session.sdk_session_id:
                return
            try:
                name = await asyncio.to_thread(claude_history.generated_name,
                                               session.sdk_session_id)
            except Exception:
                return
            if name:
                self._manager.set_auto_name(self._session_id, name)
                return
            if attempt + 1 < NAME_LOOKUP_ATTEMPTS:
                await asyncio.sleep(NAME_LOOKUP_DELAY)

    def accepts_input(self) -> bool:
        """Whether there is still a turn loop to read what `send` queues."""
        return bool(self._loop) and not self._closed and not self._failed

    def send(self, text: str, source: str = "operator") -> bool:
        """Queue a user message. Safe to call from any thread. False means the
        message was refused, not queued.

        The message is echoed into the transcript as it is queued rather than
        when the turn picks it up. The SDK never streams our own prompt back
        (only injected turns and tool results arrive as user messages), so
        without this echo a session opened with a prompt showed the reply with
        no sign of the question; and a message typed while Claude is still
        working can sit in the queue for minutes, which read as a composer that
        had swallowed it (ISSUE-026, ISSUE-028).

        The echo is also why a refusal has to be checked twice. Queueing onto a
        dead session would put the user's words in the transcript looking sent,
        which is the failure ISSUE-036 is about, so the state is read here for
        the caller's answer and again on the loop thread -- where `_failed` is
        actually written -- before anything is echoed.

        A message sent while Claude is working is not held for the next turn.
        It goes to the CLI as soon as this queue reaches it, and the turn in
        flight picks it up between tool calls; see `_run`."""
        if not self.accepts_input():
            self._refuse_send()
            return False

        message_uuid = str(uuid.uuid4())

        def _queue() -> None:
            if not self.accepts_input():
                self._refuse_send()
                return
            self._emit("text", {"role": "user", "text": text, "source": source})
            self._remember_sent(message_uuid, text)
            self._sends.put_nowait((message_uuid, text))

        self._loop.call_soon_threadsafe(_queue)
        return True

    def _remember_sent(self, message_uuid: str, text: str) -> None:
        """Keep the text of a sent message against its uuid, oldest first out.

        Only `interrupt` reads this, to say which messages a stop threw away,
        and only the recent ones can still be in the CLI's queue to throw."""
        self._sent_text[message_uuid] = text
        while len(self._sent_text) > SENT_TEXT_REMEMBERED:
            self._sent_text.pop(next(iter(self._sent_text)))

    def _refuse_send(self) -> None:
        self._emit("system", {"subtype": "error", "data": {"message": (
            "This session has stopped and cannot take new messages. "
            "Resume it from History to carry on.")}})

    @staticmethod
    async def _envelope(message_uuid: str, text: str):
        """One user message, in the shape the CLI's stdin reader expects.

        `query` builds this itself when handed a plain string, but stamps no
        uuid, and a message without one is a message the CLI cannot report on
        or cancel. Ours is the id it lists back under `cancelled`."""
        yield {
            "type": "user",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
            "uuid": message_uuid,
        }

    def set_permission_mode(self, mode: str) -> None:
        """Change the permission mode mid-session (ISSUE-012). The SDK applies
        it to the next tool call, so a mode set while a turn is running takes
        effect from the following tool onward, not retroactively."""
        if not self._loop or not self._client:
            return

        def apply() -> None:
            asyncio.ensure_future(self._set_permission_mode(mode))

        self._loop.call_soon_threadsafe(apply)

    async def _set_permission_mode(self, mode: str) -> None:
        try:
            await self._client.set_permission_mode(mode)
        except Exception as exc:
            self._emit("system", {"subtype": "error",
                                  "data": {"message": f"could not switch mode: {exc}"}})
            return
        self._permission_mode = mode
        session = self._manager.get(self._session_id)
        if session is not None:
            session.permission_mode = mode
        self._emit("system", {"subtype": "permission_mode",
                              "data": {"permission_mode": mode}})

    def commands(self) -> list[dict]:
        """Slash commands and skills this session can use.

        One list, because the CLI returns one: a skill appears here beside
        `/compact` and `/model`, and is invoked the same way. Each entry has a
        name, a description and an argument hint."""
        commands = self._server_info.get("commands")
        return commands if isinstance(commands, list) else []

    def pending_approvals(self) -> list[dict]:
        return list(self._pending_meta.values())

    def interrupt(self) -> None:
        """Stop the turn, and take everything queued behind it down too.

        Stop has to mean stop. Now that a message typed mid-turn goes straight
        to the CLI rather than waiting here, anything typed while Claude was
        working is already in the CLI's queue when the button is pressed, and a
        plain interrupt leaves it there to start a fresh turn a moment later --
        which reads as a stop that did nothing."""
        if self._loop and self._client:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._interrupt())
            )

    async def _interrupt(self) -> None:
        if self._cancels_queued_on_interrupt():
            try:
                response = await self._client._query._send_control_request(
                    {"subtype": "interrupt", "cancel_queued": True})
            except Exception:
                # An interrupt that cannot be sent the good way is still worth
                # sending: a turn left running is the worse failure.
                pass
            else:
                self._report_cancelled(response)
                return
        try:
            await self._client.interrupt()
        except Exception as exc:
            self._emit("system", {"subtype": "error",
                                  "data": {"message": f"could not stop: {exc}"}})

    def _cancels_queued_on_interrupt(self) -> bool:
        """Whether to ask for `cancel_queued` on the interrupt.

        Two things have to hold. The CLI has to honour the field, which it
        advertises as `interrupt_cancel_queued_v1`; an older one ignores it and
        interrupts without sweeping the queue, so asking costs nothing but also
        buys nothing. And the SDK has to let us ask at all: `interrupt()` takes
        no arguments and `SDKControlInterruptRequest` carries no such field, so
        the request goes out through the client's own control channel, which is
        private and may not survive an SDK upgrade.

        Capabilities arrive with the init message, which the CLI sends on the
        first turn, so they are unknown until then. Unknown is treated as
        capable: the field is documented as ignored by CLIs that predate it,
        and refusing to send it before init would disable this on exactly the
        stop most worth getting right, the one during the first turn."""
        if self._capabilities is not None \
                and "interrupt_cancel_queued_v1" not in self._capabilities:
            return False
        query = getattr(self._client, "_query", None)
        return hasattr(query, "_send_control_request")

    def _report_cancelled(self, response) -> None:
        """Say which messages the stop threw away.

        The CLI answers an interrupt with the uuids it removed from its queue.
        Those are messages the user wrote and watched appear in the transcript,
        so they are worth naming: without this they sit there looking sent and
        never answered."""
        if not isinstance(response, dict):
            return
        cancelled = response.get("cancelled")
        if not isinstance(cancelled, list) or not cancelled:
            return
        texts = [self._sent_text[u] for u in cancelled if u in self._sent_text]
        if texts:
            listed = "; ".join(_one_line(t) for t in texts)
            message = f"Stopped before reading {len(cancelled)} queued message(s): {listed}"
        else:
            message = f"Stopped before reading {len(cancelled)} queued message(s)."
        self._emit("system", {"subtype": "note", "data": {"message": message}})

    # ------------------------------------------------------------- approvals

    async def _wait_for_answer(self, request_id: str, fut: asyncio.Future,
                               tuid, name: str):
        """Wait for a client to answer, and clean up however the wait ends.

        The cancelled case is what this wrapper is for (ISSUE-052). When the CLI
        gives up on the callback the wait is happening inside, it cancels the
        task; cancelling a task cancels the future it is waiting on; and the
        card in front of the user goes on looking answerable. It is not: the
        answer arrives to find `fut.done()` and comes back as
        `stale_or_unknown_request`, after the user has read the question and
        chosen. The expiry event is what lets every client say so first.

        Popping in `finally` rather than in each caller: on the answered path
        `resolve` and `answer_question` have already popped, and pop-with-
        default makes the two paths one line instead of two branches."""
        try:
            return await fut
        except asyncio.CancelledError:
            # A closing session cancels these too, and there is no one left to
            # tell: the session is on its way out of the manager, and an event
            # saying its approval expired would arrive after the transcript it
            # belongs to has gone.
            if self._closed:
                raise
            self._emit("approval_expired", {
                "request_id": request_id,
                "tool_use_id": tuid,
                "name": name,
            })
            # The turn is not over: the CLI hands the model an error for this
            # call and carries on, so the session is working again, not still
            # waiting on someone. This is also what withdraws the desktop
            # "needs approval" notification, which keys off the status.
            self._emit_status(SessionStatus.WORKING)
            raise
        finally:
            self._pending.pop(request_id, None)
            self._pending_meta.pop(request_id, None)

    def _auto_decision(self, name: str) -> str | None:
        """What the current permission mode says about a call to `name`,
        before anyone is asked. Returns "allow" to approve it here, "defer" to
        return no decision and let the CLI's own permission handling take it,
        or None to put it to the user.

        Without this the hook's answer -- always "ask" -- overrode every mode,
        so switching to Auto mid-session changed the label in the header and
        nothing else (ISSUE-027)."""
        mode = self._permission_mode
        if mode == "bypassPermissions":
            return "allow"
        if mode == "acceptEdits" and name in EDIT_TOOLS:
            return "allow"
        if mode in MODE_DECIDES_ITSELF:
            # Checked here rather than at the top of the method: the reason is
            # that deferring parks the turn, so it has no bearing on the two
            # modes above, which answer rather than defer.
            return None if name in ALWAYS_ASK else "defer"
        return None

    async def _pre_tool_use(self, input_data, tool_use_id, context):
        tuid = input_data.get("tool_use_id")
        name = input_data.get("tool_name")
        tool_input = input_data.get("tool_input", {})

        # Before the mode is consulted, because a question is not a permission
        # and no mode should answer it on the user's behalf (ISSUE-050). The
        # hook sees every call ahead of the permission callback, so this is
        # where the question is caught.
        #
        # Allowed immediately and put to the user in `_can_use_tool`, which the
        # CLI calls next. The question used to be asked here, which spent the
        # hook's timeout on a person (ISSUE-052); the permission callback has no
        # such budget, and both see the same tool_use_id, so the answer still
        # reaches `_post_tool_use` under the key it looks for.
        if name == QUESTION_TOOL:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "the user answers this one",
            }}

        auto = self._auto_decision(name)
        if auto is not None:
            # Say so in the transcript. A tool call that runs without an
            # approval card is otherwise indistinguishable from one the user
            # forgot they approved, and the mode that let it through is the
            # thing worth seeing.
            self._emit("permission_note", {
                "tool_use_id": tuid,
                "name": name,
                "mode": self._permission_mode,
                "outcome": auto,
            })
            if auto == "allow":
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": f"permission mode {self._permission_mode}",
                }}
            # "defer": no hookSpecificOutput at all, so the CLI applies the mode
            # itself rather than reading a decision out of this hook.
            return {}

        decision = await self._ask_user(name, tuid, tool_input)

        specific = {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision["decision"],
            "permissionDecisionReason": decision.get("reason") or "",
        }
        if decision["decision"] == "allow" and decision.get("updated_input"):
            specific["updatedInput"] = decision["updated_input"]
        return {"hookSpecificOutput": specific}

    async def _ask_user(self, name, tuid, tool_input) -> dict:
        """Put a tool call to the user and wait for the answer.

        Split out of `_pre_tool_use` so the permission callback can reach the
        same approval card rather than growing a second one (ISSUE-050)."""
        request_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = self._loop.create_future()
        self._pending[request_id] = fut
        # Kept so a client that was not watching this session can still be told
        # what is waiting (the cross-session inbox, ISSUE-010).
        self._pending_meta[request_id] = {
            "request_id": request_id,
            "tool_use_id": tuid,
            "name": name,
            "input": tool_input,
        }

        self._emit("approval_request", {
            "request_id": request_id,
            "tool_use_id": tuid,
            "name": name,
            "input": tool_input,
        })
        self._emit_status(SessionStatus.AWAITING_APPROVAL)

        # {"decision", "updated_input", "reason", "decided_by"}
        decision = await self._wait_for_answer(request_id, fut, tuid, name)

        self._emit("approval_resolved", {
            "request_id": request_id,
            "tool_use_id": tuid,
            "decision": decision["decision"],
            "updated_input": decision.get("updated_input"),
            "reason": decision.get("reason"),
            # The tailnet user who answered, when the request came in with a
            # Tailscale identity (ISSUE-022). None for a token-only client.
            "decided_by": decision.get("decided_by"),
        })
        self._emit_status(SessionStatus.WORKING)
        return decision

    async def _can_use_tool(self, name, tool_input, context):
        """The CLI's own permission path, which is a different door into this
        session from the PreToolUse hook.

        The hook runs first and answers most calls, so this is reached in two
        cases: a question, and a call the CLI decided to ask about in one of
        the modes where the hook defers. Both belong in front of the user.

        A question waits here rather than in the hook because this callback has
        no timeout: measured against the bundled CLI, a permission callback that
        took 700 seconds was still allowed to answer, while a hook is abandoned
        at 600 (ISSUE-052).
        """
        tuid = getattr(context, "tool_use_id", None)
        if name == QUESTION_TOOL:
            await self._ask_question(tuid, tool_input)
            return PermissionResultAllow(updated_input=tool_input)

        decision = await self._ask_user(name, tuid, tool_input)
        if decision["decision"] == "allow":
            return PermissionResultAllow(
                updated_input=decision.get("updated_input") or tool_input)
        return PermissionResultDeny(message=decision.get("reason") or "Denied.")

    async def _ask_question(self, tuid, tool_input) -> None:
        """Show the model's question, wait for the answer, and stash it for
        `_post_tool_use` (ISSUE-050). Called from `_can_use_tool`.

        The call is always allowed afterwards: a question is not a permission,
        and refusing it would only make the model guess. Skipping is expressed
        by answering with no choices, which is what the CLI itself does when a
        question times out.
        """
        request_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = self._loop.create_future()
        self._pending[request_id] = fut
        questions = tool_input.get("questions") or []
        self._pending_meta[request_id] = {
            "request_id": request_id,
            "tool_use_id": tuid,
            "name": QUESTION_TOOL,
            "input": tool_input,
        }

        self._emit("question_request", {
            "request_id": request_id,
            "tool_use_id": tuid,
            "questions": questions,
        })
        self._emit_status(SessionStatus.AWAITING_APPROVAL)

        answer = await self._wait_for_answer(request_id, fut, tuid,
                                             QUESTION_TOOL)
        answers = answer.get("answers") or {}
        response = answer.get("response") or None

        self._emit("question_resolved", {
            "request_id": request_id,
            "tool_use_id": tuid,
            "answers": answers,
            "response": response,
            "answered_by": answer.get("decided_by"),
        })
        self._emit_status(SessionStatus.WORKING)

        # Shaped as AskUserQuestionOutput, which is what the CLI validates the
        # replacement against; a mismatch is dropped and the original kept.
        payload = {"questions": questions, "answers": answers}
        if response:
            payload["response"] = response
        if tuid:
            self._answers[tuid] = payload

    async def _post_tool_use(self, input_data, tool_use_id, context):
        """Put the user's answer in the question tool's output.

        Without this the tool returns "the user did not answer", because the
        CLI has no dialog of its own to show. Every other tool passes straight
        through."""
        if input_data.get("tool_name") != QUESTION_TOOL:
            return {}
        tuid = input_data.get("tool_use_id") or tool_use_id
        payload = self._answers.pop(tuid, None)
        if payload is None:
            return {}
        return {"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": payload,
        }}

    def answer_question(self, request_id: str, answers: dict,
                        response: str | None = None,
                        answered_by=None) -> bool:
        """Answer a pending question. Mirrors `resolve`, and shares its pending
        table, so a stale or already-answered request_id returns False."""
        fut = self._pending.pop(request_id, None)
        self._pending_meta.pop(request_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result({"answers": answers or {}, "response": response,
                        "decided_by": answered_by})
        return True

    def resolve(self, request_id: str, decision: str,
                updated_input=None, reason=None, decided_by=None) -> bool:
        """Answer a pending approval. Called from the loop thread (the WS
        handler). First valid answer wins; a stale request_id returns False."""
        fut = self._pending.pop(request_id, None)
        self._pending_meta.pop(request_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result({"decision": decision, "updated_input": updated_input,
                        "reason": reason, "decided_by": decided_by})
        return True

    def _schedule_context_read(self, force: bool = False) -> None:
        """Start a context reading beside the message loop (ISSUE-073).

        One at a time, because two in flight would race each other to the
        footer and double the control traffic a busy turn generates. A turn
        boundary that lands while one is already running sets `_context_force`
        instead of being dropped, and the runner takes a second pass for it:
        that reading is the figure the session rests at until someone types
        again, so it is the one worth not losing.
        """
        if self._closed:
            return
        if force:
            self._context_force = True
        if self._context_task is not None and not self._context_task.done():
            return
        self._context_task = asyncio.create_task(self._run_context_reads())

    async def _run_context_reads(self) -> None:
        """Drain `_context_force`, then stop. See `_schedule_context_read`."""
        try:
            while not self._closed:
                force, self._context_force = self._context_force, False
                await self._read_context_usage(force)
                if not self._context_force:
                    return
        except asyncio.CancelledError:
            raise

    async def _read_context_usage(self, force: bool = False) -> None:
        """Ask how full the context window is, and say so (ISSUE-065).

        This asks, where `_capture_plan_usage` below refuses to, and the
        difference is not inconsistency. Plan usage costs a turn against the
        very limit it reports. This is a control request handled by `_query`,
        the same family as the `get_server_info()` call in `start`, so it
        costs no turn and no tokens.

        Called on every message the loop reads and on the result that ends a
        turn, which is what `force` distinguishes: a result is worth a reading
        however recent the last one was, since it is the figure the session
        rests at until someone types again. Everything else waits out
        `CONTEXT_READ_INTERVAL` (ISSUE-069).

        Run beside the message loop, never inside it. Awaiting it there
        deadlocked against the stream it reads from: the answer arrives behind
        whatever messages are already queued, so a loop waiting for it is not
        reading the messages it is queued behind, and the request can only time
        out. Measured at 60.03s against a 25-second backlog where an unblocked
        loop answers in under a second. One session spent an hour delivering a
        finished turn at one message a minute that way (ISSUE-073).

        `_schedule_context_read` is what now keeps one of these in flight at a
        time, which is the property awaiting it here used to provide. No
        timeout is imposed on top of the SDK's own 60 seconds:
        `_send_control_request` drops its entry from
        `pending_control_responses` when its own `fail_after` fires, and
        cancelling it from outside skips that cleanup and leaks the entry
        instead.
        """
        client = self._client
        if client is None or self._closed:
            return
        now = self._loop.time() if self._loop else 0.0
        if not force and now - self._context_read_at < CONTEXT_READ_INTERVAL:
            return
        # Stamped before the request rather than after the answer, so the
        # interval measures request to request. Stamping after would add
        # however long the CLI took to the gap before the next one.
        self._context_read_at = now
        try:
            usage = await client.get_context_usage()
        except Exception:
            # A CLI that does not answer this request leaves the footer with
            # one fewer number in it. That is not worth ending a turn over,
            # and it is the state every session was in before this existed.
            return
        if not isinstance(usage, dict):
            return
        event = {
            "total_tokens": usage.get("totalTokens"),
            "max_tokens": usage.get("maxTokens"),
            "percentage": usage.get("percentage"),
            "auto_compact": bool(usage.get("isAutoCompactEnabled")),
            "threshold": usage.get("autoCompactThreshold"),
        }
        # Every event emitted is stored for the life of the session and sent
        # again to every client that subscribes, so a reading nobody could tell
        # from the one before it is not worth keeping. The footer draws the
        # percentage rounded to a whole number, so that is what decides: a turn
        # that runs for hours leaves one event per point it climbed rather than
        # one every fifteen seconds. The exact token count in the tooltip can
        # trail by up to a percent of the window as a result, which is the
        # trade (ISSUE-069).
        key = _context_display_key(event)
        if key != self._last_context_shown:
            self._last_context_shown = key
            self._emit("context_usage", event)
        # Asked on every reading, including one too close to the last to emit.
        # What it decides is whether to interrupt a turn that is about to lose
        # its context, and that does not depend on the footer having changed.
        self._maybe_ask_for_a_flush(usage)

    def _maybe_ask_for_a_flush(self, usage: dict) -> None:
        """Ask the session to write down what matters, once, before the CLI
        replaces the conversation with a summary.

        Off unless turned on. An injected prompt spends a turn and lands in the
        middle of whatever the session was doing, which is a judgement about
        someone's work rather than about a display, so `agent_options.DEFAULTS`
        leaves it to the user.

        Sent through `send` rather than `manager.queue_inject`, which is the
        route the browser's inject endpoint takes: that queue is drained by
        `window.py` alone, so a session running with no GTK window would queue
        this and never deliver it. `send` is the same path either way.

        Marked `injected` rather than `operator`, because the user did not type
        it and a transcript that says they did is wrong. The note below is what
        says the interruption happened.
        """
        if self._flush_asked or self._closed:
            return
        if not usage.get("isAutoCompactEnabled"):
            # Nothing is going to compact this conversation, so there is
            # nothing to get ahead of.
            return
        percentage = usage.get("percentage")
        if not isinstance(percentage, (int, float)):
            return
        prefs = agent_options.context_flush(config_module.load())
        if not prefs["enabled"] or percentage < prefs["percent"]:
            return
        self._flush_asked = True
        if not self.send(agent_options.CONTEXT_FLUSH_PROMPT, source="injected"):
            self._flush_asked = False
            return
        self._emit("system", {"subtype": "note", "data": {"message":
            f"Context is {percentage:.0f}% full. Asked this session to save "
            "anything worth keeping before it is compacted."}})

    def _capture_plan_usage(self, text) -> None:
        """Read plan percentages out of a `/usage` the user ran themselves.

        Nothing here asks the CLI anything: asking costs a turn against the very
        limit being reported. But when the user runs `/usage`, the answer comes
        back through this session like any other result, and the percentages in
        it are as good as any we could have paid for. Parsed on the way past and
        emitted as its own event so every client gets it, live and on replay.

        `parse` returns None for anything that is not a usage report, which is
        almost every result, so running it over each one costs a substring test.
        """
        usage = plan_usage.parse(text if isinstance(text, str) else None)
        if usage is not None:
            self._emit("plan_usage", usage.to_dict())

    # -------------------------------------------------------- message mapping

    def _handle_message(self, msg) -> None:
        if isinstance(msg, AssistantMessage):
            # The finished message supersedes whatever was still buffered.
            self._drop_pending_deltas()
            parent = getattr(msg, "parent_tool_use_id", None)
            for block in msg.content or []:
                self._handle_block(block, role="assistant", parent=parent)
        elif isinstance(msg, UserMessage):
            parent = getattr(msg, "parent_tool_use_id", None)
            # What an `Agent` call reported about the subagent it ran, when
            # this message is that call's result. Read from the same record the
            # stored transcript files under `toolUseResult`, so a live session
            # and a replayed one say the same thing (ISSUE-059).
            subagent = claude_history.subagent_meta(
                getattr(msg, "tool_use_result", None))
            for block in msg.content or []:
                self._handle_block(block, role="user", parent=parent,
                                   subagent=subagent)
        elif isinstance(msg, SystemMessage):
            data = getattr(msg, "data", {}) or {}
            if getattr(msg, "subtype", None) == "init":
                sid = data.get("session_id")
                if sid:
                    self._manager.set_sdk_session_id(self._session_id, sid)
                # What this CLI's control protocol can do, which `server_info`
                # does not carry: it answers the initialize round trip with the
                # command list and no capabilities at all. Read by
                # `_cancels_queued_on_interrupt`.
                caps = data.get("capabilities")
                if isinstance(caps, list):
                    self._capabilities = caps
            if getattr(msg, "subtype", None) == "compact_boundary":
                # The window just emptied, so the next time it fills is a new
                # occasion to ask (ISSUE-065).
                self._flush_asked = False
                # Clients clear the footer on a boundary, so the next reading
                # has to be taken and emitted rather than skipped as a repeat
                # of one describing a conversation that no longer exists
                # (ISSUE-069).
                self._last_context_shown = None
                self._context_read_at = 0.0
            self._emit("system", {"subtype": getattr(msg, "subtype", None), "data": data})
        elif isinstance(msg, ResultMessage):
            result_text = getattr(msg, "result", None)
            self._emit("usage", {
                "is_error": getattr(msg, "is_error", False),
                "total_cost_usd": getattr(msg, "total_cost_usd", None),
                "tokens": getattr(msg, "usage", None),
                "tokens_total": _model_usage_totals(getattr(msg, "model_usage", None)),
                "result_text": result_text,
            })
            self._capture_plan_usage(result_text)
        elif isinstance(msg, StreamEvent):
            self._handle_stream_event(msg)
        elif isinstance(msg, RateLimitEvent):
            info = getattr(msg, "rate_limit_info", None)
            self._note_limit_block(info)
            # Everything the CLI sent, not the four fields the banner needed.
            # `utilization` is the percentage, and it is the one field the CLI
            # leaves out on a quiet account, so the window and its reset time
            # are what a display can count on. The overage fields say whether
            # there is anything past the limit to fall back on.
            self._emit("rate_limit", {
                "status": getattr(info, "status", None),
                "rate_limit_type": getattr(info, "rate_limit_type", None),
                "resets_at": getattr(info, "resets_at", None),
                "utilization": getattr(info, "utilization", None),
                "overage_status": getattr(info, "overage_status", None),
                "overage_resets_at": getattr(info, "overage_resets_at", None),
                "overage_disabled_reason": getattr(info, "overage_disabled_reason", None),
            })
        # ConversationResetMessage is still ignored.

        # Proof the limit did not stop this turn: the model is producing under
        # it. An `allowed_warning` arrives on a turn that runs perfectly well,
        # and a `rejected` one can land on a session that is merely sitting
        # idle, so neither is worth a card on its own.
        if isinstance(msg, AssistantMessage):
            self._limit_block = None

    def _note_limit_block(self, info) -> None:
        """Hold a rate limit that actually closes the window, for `_end_turn`.

        Three things have to be true, and they are the same three the CLI asks
        before it offers to wait. The window has to be `rejected` rather than
        warned about; it has to say when it reopens, since a wait with no end
        is not one a card can offer; and overage has to be unavailable, because
        a session running on it is not stopped."""
        if getattr(info, "status", None) != "rejected":
            return
        resets_at = getattr(info, "resets_at", None)
        if not isinstance(resets_at, (int, float)):
            return
        if getattr(info, "overage_status", None) == "allowed":
            return
        self._limit_block = {
            "rate_limit_type": getattr(info, "rate_limit_type", None),
            "resets_at": int(resets_at),
        }

    def _handle_stream_event(self, msg) -> None:
        """A partial assistant message, on its way to becoming a real one.

        Only assistant **text** is forwarded. Thinking and tool-argument deltas
        are the overwhelming majority of what the model generates -- one real
        session produced 91,497 output tokens against 3,295 characters of prose
        -- and neither is legible as it arrives: a half-written tool argument
        says nothing a reader can use, and the tool card already appears the
        moment the call is made. Forwarding everything measured at 89x the
        session's entire current traffic.

        Subagent deltas are dropped for the same reason plus one more: their
        card is collapsed by default, so the text would stream into something
        nobody is looking at.
        """
        if getattr(msg, "parent_tool_use_id", None):
            return
        event = getattr(msg, "event", None)
        if not isinstance(event, dict):
            return
        if event.get("type") != "content_block_delta":
            return
        delta = event.get("delta")
        if not isinstance(delta, dict) or delta.get("type") != "text_delta":
            return
        text = delta.get("text")
        if not text:
            return

        index = event.get("index", 0)
        self._delta_buf.setdefault(index, []).append(text)
        if self._delta_task is None or self._delta_task.done():
            self._delta_task = asyncio.create_task(self._flush_deltas())

    async def _flush_deltas(self) -> None:
        """Coalesce buffered deltas into one frame per block, on a timer."""
        try:
            while not self._closed:
                await asyncio.sleep(DELTA_FLUSH_SECONDS)
                if not self._delta_buf:
                    return
                buffered, self._delta_buf = self._delta_buf, {}
                for index, parts in buffered.items():
                    self._manager.broadcast_transient(
                        self._session_id, "text_delta",
                        {"index": index, "text": "".join(parts)})
        except asyncio.CancelledError:
            raise

    def _drop_pending_deltas(self) -> None:
        """Forget un-flushed deltas because the finished block has arrived.

        Flushing them now would push the partial text out *after* the durable
        text it was a preview of, and the client would show the tail twice."""
        self._delta_buf.clear()

    def _handle_block(self, block, role: str, parent: str | None = None,
                      subagent: dict | None = None) -> None:
        """Turn one content block into an event.

        `parent` is the message's `parent_tool_use_id`: the id of the `Agent`
        tool call that spawned the subagent this block came from, or None for
        the main thread. The SDK forwards a subagent's tool_use and tool_result
        blocks whether or not `forward_subagent_text` is set, so without
        carrying this through, a subagent's work arrives looking exactly like
        the main agent's and is drawn into the top-level transcript beside it
        (ISSUE-017).

        `subagent` is what the `Agent` call reported about the subagent it ran,
        and belongs on the result rather than on the call: the agent id does
        not exist until there is an agent. Empty for every other tool.
        """
        def tag(data: dict) -> dict:
            if parent:
                data["parent_tool_use_id"] = parent
            return data

        if isinstance(block, TextBlock):
            data = {"role": role, "text": block.text}
            if role == "user":
                # Our own prompts are echoed from send() and never come back
                # down the stream, so a user text block arriving here is
                # something the CLI injected -- a skill body, a hook's output,
                # a system reminder. The renderer folds those away instead of
                # pasting a 5,000-character skill document into the transcript
                # (ISSUE-026).
                data["source"] = "injected"
            self._emit("text", tag(data))
        elif isinstance(block, ThinkingBlock):
            self._emit("thinking", tag({"text": getattr(block, "thinking", "")}))
        elif isinstance(block, ToolUseBlock):
            if block.name == AGENT_TOOL:
                self._open_agents[block.id] = str(
                    (block.input or {}).get("description") or "")
            self._emit("tool_use", tag({"tool_use_id": block.id, "name": block.name,
                                        "input": block.input}))
        elif isinstance(block, ToolResultBlock):
            # A picture in the result goes to the image store and the event
            # carries a reference to it, rather than 60,000 characters of
            # base64 riding in the event object, the in-memory event list and
            # every reconnect's backlog (ISSUE-035).
            content = images.dereference_content(
                getattr(block, "content", None),
                f"/api/sessions/{self._session_id}",
                block.tool_use_id,
                keep=True,
                session_id=self._session_id,
            )
            self._open_agents.pop(block.tool_use_id, None)
            self._emit("tool_result", tag({
                "tool_use_id": block.tool_use_id,
                "is_error": getattr(block, "is_error", None),
                "content": content,
                **(subagent or {}),
            }))

    # ---------------------------------------------------------------- emit

    def _emit(self, etype: str, data: dict) -> None:
        self._manager.add_event(self._session_id, etype, data)

    def _emit_status(self, status: SessionStatus) -> None:
        self._status = status
        self._emit("status", {"status": status.value})


class SdkRuntime:
    """Owns the shared asyncio loop and the live SdkSessions. The WS server and
    the GTK window route through it; it hides the call_soon_threadsafe dance so
    callers on the GTK main thread can start and drive sessions."""

    def __init__(self, manager: SessionManager):
        self._manager = manager
        self._sessions: dict[str, SdkSession] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        # The loop lives in the server thread, which main.py starts just after
        # the window appears. A session created in that gap has nowhere to run,
        # so callers on the GTK thread wait on this instead of racing.
        self._loop_ready = threading.Event()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_ready.set()

    async def create(self, session_id: str, workdir: str, permission_mode: str = "default",
                     resume: str | None = None, initial_prompt: str | None = None) -> None:
        if resume:
            self._seed_history(session_id, resume)
        session = SdkSession(self._manager, session_id, workdir, permission_mode, resume)
        self._sessions[session_id] = session
        try:
            await session.start(initial_prompt)
        except Exception:
            # start() has already emitted the error event and status; drop the
            # half-built session so send/resolve report it as unknown.
            self._sessions.pop(session_id, None)
            raise

    def create_threadsafe(self, session_id: str, workdir: str, permission_mode: str = "default",
                          resume: str | None = None, initial_prompt: str | None = None) -> None:
        """Start a session from a non-loop thread (the GTK main thread)."""
        if not self._loop_ready.wait(timeout=LOOP_START_TIMEOUT):
            self._manager.add_event(session_id, "system", {
                "subtype": "error",
                "data": {"message": "the session runtime did not start"},
            })
            self._manager.add_event(session_id, "status", {"status": SessionStatus.ERROR.value})
            return

        def _start() -> None:
            task = asyncio.ensure_future(
                self.create(session_id, workdir, permission_mode, resume, initial_prompt)
            )
            # Without this a failed start is a silently swallowed task exception
            # and the session just sits there looking hung.
            task.add_done_callback(_report_start_failure)

        self._loop.call_soon_threadsafe(_start)

    def _seed_history(self, session_id: str, sdk_session_id: str) -> None:
        """Replay a resumed conversation into the event bus before the session
        starts, so the pane opens showing what was already said (ISSUE-009).
        The SDK continues the conversation but never replays it, and history
        the model has but the user cannot see is the wrong way round.

        Seeded events go through the same add_event path as live ones, so they
        get the same seq ordering and render identically."""
        self._manager.set_sdk_session_id(session_id, sdk_session_id)
        session = self._manager.get(session_id)
        if session is not None:
            # Seeded history carries no usage events, so any cost and token
            # total this session reports covers only what it runs from here.
            session.totals_cover_this_run_only = True
        # The conversation was named before it was put down; the name is a
        # better row than the folder it ran in, and it costs the same file
        # scan the seeding above already does.
        name = claude_history.generated_name(sdk_session_id)
        if name:
            self._manager.set_auto_name(session_id, name)
        for etype, data in claude_history.events_for_session(sdk_session_id):
            self._manager.add_event(session_id, etype, data)

    def has(self, session_id: str) -> bool:
        return session_id in self._sessions

    def send(self, session_id: str, text: str) -> bool:
        """False when there is no live session by that id, or when the one
        there is has stopped and cannot take the message (ISSUE-036)."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        return session.send(text)

    def set_permission_mode(self, session_id: str, mode: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.set_permission_mode(mode)
        return True

    def commands(self, session_id: str) -> list[dict] | None:
        session = self._sessions.get(session_id)
        return None if session is None else session.commands()

    def interrupt(self, session_id: str) -> bool:
        """Stop the turn a session is in the middle of (ISSUE-033).

        SdkSession has had this since M1 with nothing wired to it. It is worth
        wiring now because streaming makes it usable: watching an answer form is
        only useful if it can be stopped."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.interrupt()
        return True

    def pending_approvals(self) -> list[dict]:
        """Everything waiting on a decision, across every live session. The
        inbox reads this once on connect; live changes arrive as events."""
        out = []
        for session_id, session in self._sessions.items():
            for approval in session.pending_approvals():
                out.append({"session_id": session_id, **approval})
        return out

    def resolve(self, session_id: str, request_id: str, decision: str,
                updated_input=None, reason=None, decided_by=None) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        return session.resolve(request_id, decision, updated_input, reason, decided_by)

    def answer_question(self, session_id: str, request_id: str, answers: dict,
                        response: str | None = None, answered_by=None) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        return session.answer_question(request_id, answers, response, answered_by)

    async def close(self, session_id: str) -> bool:
        """Disconnect one session and forget it. False when it is not one of
        ours, which a terminal session never is: the manager still holds it and
        the caller still removes it from there."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.close()
        return True

    def close_threadsafe(self, session_id: str) -> None:
        """Close a session from a non-loop thread (the GTK main thread).

        Nothing waits for the disconnect. The window has already taken the row
        and the pane away by the time this runs, and holding the UI for a
        subprocess to go is a freeze the user cannot explain."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self.close(session_id))
        )

    async def close_all(self) -> None:
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()

    def close_all_threadsafe(self, timeout: float = SHUTDOWN_TIMEOUT) -> None:
        """Disconnect every live session from a non-loop thread, and wait.

        This one waits where `close_threadsafe` does not, because it runs on
        the way out: the loop lives in a daemon thread, so the moment the GTK
        main loop returns the process ends and that thread is cut off with
        whatever `claude` subprocesses it owns still attached. Waiting is the
        whole point (ISSUE-037).

        Bounded, though. A subprocess that will not go is not worth hanging a
        quit on -- the process is about to exit and take it with it either
        way -- so the timeout is a ceiling on how long a clean exit is allowed
        to cost, not a promise every session answered.
        """
        if self._loop is None or not self._sessions:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self.close_all(), self._loop)
            future.result(timeout)
        except Exception:
            pass  # quitting is no place to raise
