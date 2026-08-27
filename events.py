"""The canonical transcript event model (ISSUE-003).

One event shape, produced in the backend and rendered by both the WebKitGTK
pane and the browser. The wire format is defined in docs/transcript-protocol.md;
this module is the Python side of that contract. Keep the two in step.

Events are in-memory per session for M1. The durable record is the SDK's own
JSONL transcript under ~/.claude/projects; ISSUE-009 reconstructs history from
there. The SQLite DB continues to hold session metadata only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    """Derived from events, not from output timing (for sdk sessions)."""

    INITIALIZING = "initializing"
    WORKING = "working"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    DONE = "done"
    ERROR = "error"


@dataclass
class TranscriptEvent:
    """One event in a session transcript.

    Type-specific fields live in `data` and are flattened to the top level on
    the wire, so `to_dict()` matches the shapes in docs/transcript-protocol.md
    (e.g. a text event serializes as {type, session_id, seq, ts, role, text}).
    """

    type: str
    session_id: str
    seq: int
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "seq": self.seq,
            "ts": self.ts,
            **self.data,
        }


# Event types the wire understands, for validation and reference. The type-
# specific fields for each are documented in docs/transcript-protocol.md.
EVENT_TYPES = frozenset({
    "system", "text", "thinking", "tool_use", "tool_result",
    "approval_request", "approval_resolved", "approval_expired",
    "question_request", "question_resolved", "permission_note",
    "usage", "rate_limit", "plan_usage", "status",
})
