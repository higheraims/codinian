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
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

import agent_options
import claude_history
import config as config_module
import images
import plan_usage
from events import SessionStatus
from session import SessionManager


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
MODE_DECIDES_ITSELF = frozenset({"auto", "dontAsk"})

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
        self._sends: asyncio.Queue[str | None] = asyncio.Queue()
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
        # Filled at connect; see start(). Empty when the CLI did not answer.
        self._server_info: dict = {}
        # The running look for this conversation's generated title, if any.
        self._name_task: asyncio.Task | None = None

    # ------------------------------------------------------------- lifecycle

    async def start(self, initial_prompt: str | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        options = ClaudeAgentOptions(
            cwd=self._workdir,
            permission_mode=self._permission_mode,
            hooks={"PreToolUse": [HookMatcher(hooks=[self._pre_tool_use])]},
            resume=self._resume,
            # System prompt, model, effort and thinking visibility, from the
            # user's settings (ISSUE-032). Read at start rather than held on
            # the session, so a change applies to the next session without a
            # restart. Only settings actually chosen appear in the mapping.
            **agent_options.options_kwargs(config_module.load()),
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
        if self._run_task:
            self._run_task.cancel()
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass

    # -------------------------------------------------------- turn processing

    async def _run(self) -> None:
        """The one task that drives every turn. It is created once in `start()`
        and never recreated, so whatever ends it ends the session.

        That makes the two kinds of failure worth separating. A raise out of
        `_handle_message` is a bug in our own mapping of one block, and killing
        a working conversation over an unrenderable block is the wrong trade,
        so those are reported per message and the turn carries on. A raise from
        the client -- the pipe to the `claude` subprocess going away -- has
        nothing left to carry on with, so the loop stops and says so, and
        `send` refuses from then on rather than queueing into the dark
        (ISSUE-036).
        """
        try:
            while not self._closed:
                text = await self._sends.get()
                if text is None:
                    break
                self._emit_status(SessionStatus.WORKING)
                await self._client.query(text)
                async for msg in self._client.receive_response():
                    try:
                        self._handle_message(msg)
                    except Exception as exc:
                        self._emit("system", {"subtype": "error", "data": {
                            "message": f"could not render one message: {exc}"}})
                self._schedule_name_lookup()
                if not self._closed:
                    self._emit_status(SessionStatus.AWAITING_INPUT)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface, do not swallow
            self._fail(str(exc))

    def _fail(self, message: str) -> None:
        """The turn loop has stopped for good. Say so, refuse further sends,
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

    def send(self, text: str) -> bool:
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
        actually written -- before anything is echoed."""
        if not self.accepts_input():
            self._refuse_send()
            return False

        def _queue() -> None:
            if not self.accepts_input():
                self._refuse_send()
                return
            self._emit("text", {"role": "user", "text": text, "source": "operator"})
            self._sends.put_nowait(text)

        self._loop.call_soon_threadsafe(_queue)
        return True

    def _refuse_send(self) -> None:
        self._emit("system", {"subtype": "error", "data": {"message": (
            "This session has stopped and cannot take new messages. "
            "Resume it from History to carry on.")}})

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
        if self._loop and self._client:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._client.interrupt())
            )

    # ------------------------------------------------------------- approvals

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
            return "defer"
        return None

    async def _pre_tool_use(self, input_data, tool_use_id, context):
        tuid = input_data.get("tool_use_id")
        name = input_data.get("tool_name")
        tool_input = input_data.get("tool_input", {})

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

        decision = await fut  # {"decision", "updated_input", "reason", "decided_by"}

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

        specific = {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision["decision"],
            "permissionDecisionReason": decision.get("reason") or "",
        }
        if decision["decision"] == "allow" and decision.get("updated_input"):
            specific["updatedInput"] = decision["updated_input"]
        return {"hookSpecificOutput": specific}

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
            for block in msg.content or []:
                self._handle_block(block, role="user", parent=parent)
        elif isinstance(msg, SystemMessage):
            data = getattr(msg, "data", {}) or {}
            if getattr(msg, "subtype", None) == "init":
                sid = data.get("session_id")
                if sid:
                    self._manager.set_sdk_session_id(self._session_id, sid)
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

    def _handle_block(self, block, role: str, parent: str | None = None) -> None:
        """Turn one content block into an event.

        `parent` is the message's `parent_tool_use_id`: the id of the `Agent`
        tool call that spawned the subagent this block came from, or None for
        the main thread. The SDK forwards a subagent's tool_use and tool_result
        blocks whether or not `forward_subagent_text` is set, so without
        carrying this through, a subagent's work arrives looking exactly like
        the main agent's and is drawn into the top-level transcript beside it
        (ISSUE-017).
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
            self._emit("tool_result", tag({
                "tool_use_id": block.tool_use_id,
                "is_error": getattr(block, "is_error", None),
                "content": content,
            }))

    # ---------------------------------------------------------------- emit

    def _emit(self, etype: str, data: dict) -> None:
        self._manager.add_event(self._session_id, etype, data)

    def _emit_status(self, status: SessionStatus) -> None:
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
