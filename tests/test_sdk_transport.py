"""What the size of one CLI message costs a session (ISSUE-078).

These run a stub `claude` as a real subprocess, because the limit under test
belongs to the SDK's transport: the size is checked while lines are framed off
the pipe, before there is a message to hand to `_handle_message`. A fake client
handing back objects never reaches that code.

The stub speaks the little of the control protocol `connect()` needs, then
answers one prompt with a tool result carrying an image, which is the shape that
found this: a `Read` of a mockup PNG.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import stat

import pytest

from codinian import sdk_session
from codinian.events import SessionStatus
from codinian.sdk_session import SdkSession
from codinian.session import Session, SessionManager

# Over the SDK's 1 MiB default and well under MAX_BUFFER_BYTES, so one stub
# serves both tests: at the default this line ends the session, at ours it
# arrives.
PAYLOAD_BYTES = 2 * 1024 * 1024

STUB = '''#!/usr/bin/env python3
import json, sys

PAYLOAD = "A" * %d

def say(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if msg.get("type") == "control_request":
        say({"type": "control_response",
             "response": {"subtype": "success", "request_id": msg["request_id"],
                          "commands": [], "output_styles": []}})
        continue
    if msg.get("type") != "user":
        continue
    say({"type": "assistant", "session_id": "stub",
         "message": {"id": "m1", "role": "assistant", "model": "stub",
                     "content": [{"type": "tool_use", "id": "toolu_01",
                                  "name": "Read",
                                  "input": {"file_path": "/tmp/mockup.png"}}],
                     "stop_reason": None, "usage": {}}})
    # The line that does it: one image, one message, past the default ceiling.
    say({"type": "user", "session_id": "stub",
         "message": {"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                                  "content": [{"type": "image",
                                               "source": {"type": "base64",
                                                          "media_type": "image/png",
                                                          "data": PAYLOAD}}]}]}})
    say({"type": "result", "subtype": "success", "session_id": "stub",
         "duration_ms": 1, "duration_api_ms": 1, "is_error": False,
         "num_turns": 1, "result": "done"})
''' % PAYLOAD_BYTES


def async_test(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


@pytest.fixture
def stub_cli(tmp_path):
    path = tmp_path / "claude"
    path.write_text(STUB)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def manager():
    mgr = SessionManager()
    mgr.add(Session(id="s1", kind="sdk"))
    return mgr


async def one_turn(manager, monkeypatch, stub_cli, workdir):
    """Run the stub's single turn to its end, or to the session failing."""
    monkeypatch.setattr(sdk_session.config_module, "load", lambda: {})
    monkeypatch.setattr(sdk_session.claude_cli, "options_kwargs",
                        lambda config: {"cli_path": stub_cli})
    # Nothing here is really gated: the stub narrates a tool call rather than
    # asking to make one, so the mode is the default only to keep the SDK from
    # warning about a shadowed `can_use_tool`.
    session = SdkSession(manager, "s1", str(workdir))
    await session.start("read the mockup")
    try:
        for _ in range(300):
            await asyncio.sleep(0.02)
            if kinds(manager) & {"usage"} or failed(manager):
                return session
        pytest.fail(f"turn never ended; events: {sorted(kinds(manager))}")
    finally:
        await session.close()


def kinds(manager):
    return {e["type"] for e in manager.get_events("s1")}


def failed(manager):
    # get_events flattens each event's data to the top level.
    return any(e["type"] == "status" and e["status"] == SessionStatus.ERROR.value
               for e in manager.get_events("s1"))


def errors(manager):
    return [e.get("data", {}).get("message", "") for e in manager.get_events("s1")
            if e["type"] == "system" and e.get("subtype") == "error"]


def results(manager):
    return [e for e in manager.get_events("s1") if e["type"] == "tool_result"]


@async_test
async def test_an_image_in_a_tool_result_does_not_end_the_session(
        manager, monkeypatch, stub_cli, tmp_path):
    await one_turn(manager, monkeypatch, stub_cli, tmp_path)

    assert not failed(manager), errors(manager)
    assert errors(manager) == []
    # The turn reached its end, and the picture came with it.
    assert "usage" in kinds(manager)
    blocks = results(manager)[0]["content"]
    assert [b["type"] for b in blocks] == ["image"]
    # Carried as a reference, not as the 2 MiB it arrived as (ISSUE-035).
    source = blocks[0]["source"]
    assert source["type"] == "codinian_ref"
    assert source["bytes"] == len(base64.b64decode("A" * PAYLOAD_BYTES))


@async_test
async def test_the_sdk_default_would_have_ended_it(
        manager, monkeypatch, stub_cli, tmp_path):
    # Why MAX_BUFFER_BYTES is set at all, and the guard against it being
    # dropped: at the SDK's 1 MiB the same line takes the session down, while
    # the `claude` process behind it is untouched and still working.
    monkeypatch.setattr(sdk_session, "MAX_BUFFER_BYTES", 1024 * 1024)

    await one_turn(manager, monkeypatch, stub_cli, tmp_path)

    assert failed(manager)
    assert any("exceeded maximum buffer size" in m for m in errors(manager))
    assert results(manager) == []
