"""Reading plan limits out of what the CLI already says.

On a subscription the dollar figure a session reports is trivia -- Anthropic's
own docs say the session cost "isn't relevant for billing purposes" for Pro and
Max subscribers. What constrains the work is the plan's rolling windows: a
five-hour session window, a weekly window across all models, and a weekly window
per model.

Two things carry that information, and this module is only concerned with the
second:

- The CLI pushes a `rate_limit_event` at the start of a turn, free and unasked.
  It names the window in force, the status, the reset time and the state of any
  overage. It has a `utilization` field, but an `allowed` event on a quiet
  account arrives without one, so it does not reliably carry a percentage.
  `sdk_session.py` forwards that event as-is.

- `/usage` answers with the percentages for every window at once. Asking costs a
  turn, so nothing here asks. Instead, when the *user* runs `/usage` in a
  session, the answer passes through the transcript, and `parse` below reads the
  percentages out of it on the way past. The numbers cost exactly what the user
  chose to spend and nothing more.

The percentages are the CLI's own, computed from session history on this
machine, so they exclude other devices and claude.ai. That caveat travels with
the numbers rather than being dropped.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

# "Current session: 24% used · resets Aug 21, 10:39am (America/New_York)".
# Everything after "used" is captured loosely and mined for a reset time
# separately, so an unfamiliar separator (a bullet instead of ·) or a "<1%"
# reading costs at most the reset text, never the whole window. `<?\d+` keeps
# the "<1% used" the CLI prints for a near-empty window from dropping the line.
_SESSION_RE = re.compile(
    r"^Current session:\s*<?(\d+)%\s*used\b(.*)$", re.MULTILINE)
# "Current week (all models): 6% used · resets Aug 26, 8am (America/New_York)",
# and the same shape per model, e.g. "Current week (Opus)".
_WEEK_RE = re.compile(
    r"^Current week\s*\(([^)]+)\):\s*<?(\d+)%\s*used\b(.*)$", re.MULTILINE)
# The reset time, pulled from whatever trailed "used" on the line above.
_RESETS_RE = re.compile(r"resets\s+(.+?)\s*$")


def _resets(tail: str) -> str:
    match = _RESETS_RE.search(tail)
    return match.group(1).strip() if match else ""

NOTE = ("Approximate, from sessions on this machine only -- other devices and "
        "claude.ai are not counted.")


@dataclass
class Window:
    """One plan limit window and how much of it is gone."""

    key: str          # "session", "week", or "week:<model>"
    label: str        # for display, e.g. "Week (Opus)"
    percent: int      # 0-100, as the CLI reports it
    resets: str = ""  # the CLI's own wording, timezone included


@dataclass
class PlanUsage:
    windows: list[Window] = field(default_factory=list)
    captured_at: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "windows": [asdict(w) for w in self.windows],
            "captured_at": self.captured_at,
            "note": self.note,
        }


def parse(text: str) -> PlanUsage | None:
    """The plan windows in `text`, or None if it does not describe any.

    Tolerant by design. This is the CLI's human-facing output, not a documented
    format: an unrecognised line is skipped rather than failing the parse, and a
    release that renames a window costs that window rather than the whole
    reading. Returning None for text that is not a usage report is what lets
    this be run speculatively over passing transcript text.
    """
    if not text or "% used" not in text:
        return None  # the cheap reject: almost every string, at no cost

    usage = PlanUsage(captured_at=time.time())

    match = _SESSION_RE.search(text)
    if match:
        usage.windows.append(Window(
            key="session",
            label="Session",
            percent=int(match.group(1)),
            resets=_resets(match.group(2)),
        ))

    for scope, percent, tail in _WEEK_RE.findall(text):
        scope = scope.strip()
        all_models = scope.lower() == "all models"
        usage.windows.append(Window(
            key="week" if all_models else f"week:{scope.lower()}",
            label="Week" if all_models else f"Week ({scope})",
            percent=int(percent),
            resets=_resets(tail),
        ))

    if not usage.windows:
        return None
    if "Approximate" in text:
        usage.note = NOTE
    return usage
