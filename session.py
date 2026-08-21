import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from events import SessionStatus, TranscriptEvent

# How a terminal session's timing-based Status maps onto the transcript
# protocol's SessionStatus vocabulary, so both session kinds share one status
# field on the wire (docs/transcript-protocol.md). Terminal sessions have no
# notion of "awaiting approval", so idle/stuck fold into awaiting_input.
_TERMINAL_STATUS_MAP = {
    "running": SessionStatus.WORKING,
    "idle": SessionStatus.AWAITING_INPUT,
    "stuck": SessionStatus.AWAITING_INPUT,
    "done": SessionStatus.DONE,
    "error": SessionStatus.ERROR,
}


# What the SDK accepts for a session's permission mode. Lives here rather than
# in the server so every entry point that takes one from a client -- the
# WebSocket `create`, the project workspace's resume (ISSUE-024) -- checks it
# against the same list, and a typo is answered rather than passed down to fail
# mid-turn.
PERMISSION_MODES = frozenset({
    "default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto",
})


# How a session finds the project it belongs to (ISSUE-020). Injected by
# main.py rather than imported, so session.py keeps no dependency on the
# project registry and a session still works when no project is registered.
# Returns a project id, or None when the workdir is not inside one.
_project_resolver = None


def set_project_resolver(resolver) -> None:
    """Install the workdir-to-project-id lookup used by Session.meta()."""
    global _project_resolver
    _project_resolver = resolver


class Status(str, Enum):
    RUNNING = "running"
    IDLE = "idle"
    STUCK = "stuck"
    DONE = "done"
    ERROR = "error"


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    # True once a person has named this session, which stops the CLI's
    # generated title from being adopted over the top of their choice.
    name_is_custom: bool = False
    workdir: str = field(default_factory=lambda: str(Path.home()))
    status: Status = Status.RUNNING
    created_at: datetime = field(default_factory=datetime.now)
    last_output_at: datetime | None = None
    pid: int | None = None
    resume_session_id: str | None = None
    # SDK-session fields (unused by terminal sessions).
    kind: str = "terminal"                       # "terminal" | "sdk"
    permission_mode: str = "default"
    sdk_session_id: str | None = None            # the SDK's own uuid, once known
    sdk_status: SessionStatus | None = None      # event-derived status for sdk kind
    # Both are cumulative for the conversation: cost as the SDK reports it, and
    # tokens summed from its per-model breakdown (ISSUE-011).
    cost_usd: float = 0.0
    tokens: dict = field(default_factory=lambda: {
        "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
    })
    # True when this session resumed an earlier conversation, whose cost and
    # tokens are not in these totals. Says so rather than showing a number that
    # looks like the whole conversation.
    totals_cover_this_run_only: bool = False

    def wire_status(self) -> str:
        """The unified SessionStatus string for the transcript protocol."""
        if self.kind == "sdk":
            return (self.sdk_status or SessionStatus.INITIALIZING).value
        mapped = _TERMINAL_STATUS_MAP.get(self.status.value, SessionStatus.WORKING)
        return mapped.value

    def project_id(self) -> str | None:
        """The registered project this session's workdir sits inside, if any.
        Derived on read rather than stored, so registering a folder claims the
        sessions already running inside it (docs/project-workspace-protocol.md)."""
        if _project_resolver is None:
            return None
        return _project_resolver(self.workdir)

    def meta(self) -> dict:
        """SessionMeta, per docs/transcript-protocol.md."""
        return {
            "id": self.id,
            "name": self.name,
            "name_is_custom": self.name_is_custom,
            "workdir": self.workdir,
            "kind": self.kind,
            "status": self.wire_status(),
            "created_at": self.created_at.isoformat(),
            "sdk_session_id": self.sdk_session_id,
            "permission_mode": self.permission_mode,
            "cost_usd": self.cost_usd,
            "tokens": dict(self.tokens),
            "totals_cover_this_run_only": self.totals_cover_this_run_only,
            "project_id": self.project_id(),
        }


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._listeners: list = []
        self._inject_queue: queue.Queue = queue.Queue()
        self._outputs: dict[str, str] = {}
        self._lock = threading.Lock()
        # Transcript event bus (ISSUE-003): per-session ordered events, a
        # per-session seq counter, and subscribers that receive every append.
        self._events: dict[str, list[TranscriptEvent]] = {}
        self._seq: dict[str, int] = {}
        self._event_subs: list = []

    def add(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.id] = session
        self._notify()

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def update_status(self, session_id: str, status: Status) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].status = status
        self._notify()

    def record_output(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].last_output_at = datetime.now()
                if self._sessions[session_id].status in (Status.IDLE, Status.STUCK):
                    self._sessions[session_id].status = Status.RUNNING

    def set_pid(self, session_id: str, pid: int) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].pid = pid

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._outputs.pop(session_id, None)
            self._events.pop(session_id, None)
            self._seq.pop(session_id, None)
        self._notify()

    def set_output(self, session_id: str, text: str) -> None:
        with self._lock:
            self._outputs[session_id] = text

    def get_output(self, session_id: str) -> str:
        with self._lock:
            return self._outputs.get(session_id, "")

    def queue_inject(self, session_id: str, text: str) -> None:
        self._inject_queue.put((session_id, text))

    def drain_injects(self) -> list[tuple[str, str]]:
        items = []
        while True:
            try:
                items.append(self._inject_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": s.id,
                    "name": s.name,
                    "workdir": s.workdir,
                    "status": s.status.value,
                    "created_at": s.created_at.isoformat(),
                    "last_output_at": s.last_output_at.isoformat() if s.last_output_at else None,
                    "pid": s.pid,
                    "resume_session_id": s.resume_session_id,
                }
                for s in self._sessions.values()
            ]

    def on_change(self, callback) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for cb in self._listeners:
            cb()

    # -------------------------------------------------------------- event bus

    def subscribe_events(self, callback) -> None:
        """Register a callback invoked as callback(session_id, event_dict) for
        every appended event. Called from whichever thread appended; the
        subscriber marshals to its own context (asyncio loop, GLib.idle_add)."""
        self._event_subs.append(callback)

    def unsubscribe_events(self, callback) -> None:
        if callback in self._event_subs:
            self._event_subs.remove(callback)

    def add_event(self, session_id: str, etype: str, data: dict) -> TranscriptEvent | None:
        """Assign the next seq, build and store the event, fold any status/cost
        change into the session, then broadcast. seq assignment is under the
        lock so concurrent appends never collide; subscribers are called
        outside the lock so a slow subscriber cannot stall the driver."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            seq = self._seq.get(session_id, 0)
            self._seq[session_id] = seq + 1
            event = TranscriptEvent(etype, session_id, seq, dict(data))
            self._events.setdefault(session_id, []).append(event)
            if etype == "status":
                try:
                    session.sdk_status = SessionStatus(data.get("status"))
                except ValueError:
                    pass
            elif etype == "usage":
                if data.get("total_cost_usd") is not None:
                    session.cost_usd = data["total_cost_usd"]
                total = data.get("tokens_total")
                if isinstance(total, dict):
                    # Already cumulative for the conversation, so assign.
                    session.tokens.update(total)
                else:
                    self._add_tokens(session, data.get("tokens"))

        payload = event.to_dict()
        for cb in list(self._event_subs):
            cb(session_id, payload)
        # Status and usage change what the session list shows, and a client
        # only receives events for the session it has open, so without this a
        # sidebar entry for any other session keeps its status dot and cost
        # from whenever the list was last sent. Limited to these two types so
        # ordinary text and tool events do not rebroadcast the whole list.
        if etype in ("status", "usage"):
            self._notify()
        return event

    def broadcast_transient(self, session_id: str, etype: str, data: dict) -> None:
        """Send an event to whoever is watching, without storing it (ISSUE-033).

        Streaming deltas are the reason this exists. Every durable event is
        appended to `_events` for the life of the session and replayed in full
        on every `subscribe`; per-token deltas would put thousands of entries a
        turn in that list and re-send them on every reconnect, to rebuild text
        the client could have received as one block.

        So a delta is broadcast and forgotten. It carries no `seq`, because it
        has no place in the ordering a reconnecting client catches up on, and
        the finished block that follows is the durable record of the same text.
        A client that ignores transient events entirely still receives a
        complete, correct transcript.
        """
        with self._lock:
            if session_id not in self._sessions:
                return
        payload = {"type": etype, "session_id": session_id, "transient": True, **data}
        for cb in list(self._event_subs):
            cb(session_id, payload)

    @staticmethod
    def _add_tokens(session: Session, usage) -> None:
        """Fold one result's token counts into the session total. Called under
        the lock. Only used when the result carried no cumulative
        `tokens_total`; those counts are per turn, so these accumulate."""
        if not isinstance(usage, dict):
            return
        for key, field_name in (("input_tokens", "input"),
                                ("output_tokens", "output"),
                                ("cache_read_input_tokens", "cache_read"),
                                ("cache_creation_input_tokens", "cache_creation")):
            value = usage.get(key)
            if isinstance(value, int):
                session.tokens[field_name] += value

    def get_events(self, session_id: str) -> list[dict]:
        with self._lock:
            return [e.to_dict() for e in self._events.get(session_id, [])]

    def rename(self, session_id: str, name: str) -> bool:
        """Name a session by hand. The name sticks: a session named this way is
        not renamed again by the CLI's generated title."""
        name = name.strip()
        if not name:
            return False
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.name = name
            session.name_is_custom = True
        self._notify()
        return True

    def set_auto_name(self, session_id: str, name: str) -> bool:
        """Adopt a name the session did not choose for itself -- the title the
        CLI generates for the conversation. Ignored once the session has been
        renamed by hand, and when the name has not actually changed, so this
        can be called after every turn."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.name_is_custom or session.name == name:
                return False
            session.name = name
        self._notify()
        return True

    def set_sdk_session_id(self, session_id: str, sdk_session_id: str) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].sdk_session_id = sdk_session_id
        self._notify()

    def sessions_meta(self) -> list[dict]:
        with self._lock:
            return [s.meta() for s in self._sessions.values()]

    def meta(self, session_id: str) -> dict | None:
        with self._lock:
            session = self._sessions.get(session_id)
            return session.meta() if session else None
