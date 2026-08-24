"""The remote HTTP + WebSocket server (ISSUE-005).

Alongside the original REST endpoints (kept for the terminal-mirror path and
external scripts), this serves a WebSocket at /api/ws that speaks the transcript
protocol in docs/transcript-protocol.md: it streams TranscriptEvents to
subscribed clients and accepts send / resolve / subscribe / create from them.

Every `/api/` route and the WebSocket require the token from config.py
(ISSUE-007). Static assets are served without one: they are the same HTML, CSS
and JS for everybody and carry no session data, and the page needs to load
before it can ask for a token. The browser WebSocket API cannot set headers, so
the token is also accepted as a `token` query parameter.
"""

import asyncio
import json
import secrets
import sys
import traceback
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import WSMsgType, web

sys.path.insert(0, str(Path(__file__).parent.parent))
import config as config_module  # noqa: E402
import tailscale  # noqa: E402
from remote import projects_api  # noqa: E402
import agent_options  # noqa: E402
import claude_history  # noqa: E402
import history_search  # noqa: E402
import project  # noqa: E402
from session import PERMISSION_MODES, Session, SessionManager  # noqa: E402
from sdk_session import SdkRuntime  # noqa: E402

STATIC = Path(__file__).parent / "static"


class ClientHub:
    """Tracks connected WebSocket clients and which sessions each subscribes to,
    and pushes events/session-list updates to them. All sends are scheduled on
    the asyncio loop, so callers on other threads never touch a socket."""

    def __init__(self, loop: asyncio.AbstractEventLoop, manager: SessionManager):
        self._loop = loop
        self._manager = manager
        self._subs: dict[web.WebSocketResponse, set[str]] = {}
        # Clients watching every session's approvals at once (ISSUE-010),
        # independently of which single session they have open.
        self._inbox: set[web.WebSocketResponse] = set()

    def add(self, ws: web.WebSocketResponse) -> None:
        self._subs[ws] = set()

    def remove(self, ws: web.WebSocketResponse) -> None:
        self._subs.pop(ws, None)
        self._inbox.discard(ws)

    def subscribe_inbox(self, ws: web.WebSocketResponse) -> None:
        self._inbox.add(ws)

    def unsubscribe_inbox(self, ws: web.WebSocketResponse) -> None:
        self._inbox.discard(ws)

    def subscribe(self, ws: web.WebSocketResponse, session_id: str) -> None:
        self._subs.setdefault(ws, set()).add(session_id)

    def unsubscribe(self, ws: web.WebSocketResponse, session_id: str) -> None:
        self._subs.get(ws, set()).discard(session_id)

    def _send(self, ws: web.WebSocketResponse, payload: dict) -> None:
        if not ws.closed:
            asyncio.ensure_future(ws.send_json(payload))

    def broadcast_event(self, session_id: str, event: dict) -> None:
        """Manager event-bus subscriber. Runs on the loop thread (the driver
        appends there), so scheduling sends is safe."""
        message = {"t": "event", "event": event}
        for ws, subs in list(self._subs.items()):
            if session_id in subs:
                self._send(ws, message)

        # Approvals also go to inbox clients, whatever they have open. Sent as
        # a distinct message so a client watching this session updates its
        # transcript from `event` and its inbox from this, without either
        # having to guess which one an `event` was meant for.
        if event.get("type") in ("approval_request", "approval_resolved"):
            inbox_message = {"t": "inbox_event", "event": event,
                             "session": self._manager.meta(session_id)}
            for ws in list(self._inbox):
                self._send(ws, inbox_message)

    def broadcast_sessions(self, sessions: list[dict]) -> None:
        message = {"t": "sessions", "sessions": sessions}
        for ws in list(self._subs):
            self._send(ws, message)


def _request_token(request: web.Request) -> str:
    """The token a request presents, from the Authorization header, a dedicated
    header, or the query string. The query form exists because the browser
    WebSocket API cannot send headers."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    return request.headers.get("X-Codinian-Token") or request.query.get("token", "")


def _same_origin(request: web.Request) -> bool:
    """Reject cross-site requests. A page on another origin cannot read our
    responses, but it can send them, so without this a site the user happens to
    have open could drive the API using a token it guessed or was given.

    Defence in depth, not the security boundary. Both headers this compares are
    supplied by the caller, and under DNS rebinding they stay consistent with
    each other while pointing at us, so the check passes. The token is what
    actually protects a state-changing route; this only removes the easiest way
    to reach one (ISSUE-041).
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True  # curl, the desktop pane's fetches, and non-browser clients
    return urlparse(origin).netloc == request.headers.get("Host", "")


def _identity_may_authenticate(request: web.Request, config: dict) -> bool:
    """Whether a Tailscale identity header is allowed to stand in for the token.

    Three conditions, all required, and even together they are weaker than the
    token. The setting must be on. The bind must be loopback, because on an open
    bind anything on the network could send the header. The request must arrive
    from loopback, and `tailscale serve` must actually be proxying this port, so
    the header is at least consistent with a proxy that exists.

    What none of this establishes is that the request came through that proxy:
    tailscaled connects from 127.0.0.1 like any other local process. This is why
    the setting is off by default (ISSUE-022).
    """
    if not config.get("trust_tailscale_identity"):
        return False
    if config.get("bind") != config_module.LOOPBACK:
        return False
    if request.remote not in ("127.0.0.1", "::1"):
        return False
    return tailscale.serve_target(config["port"]) is not None


def _auth_middleware(config: dict):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if not request.path.startswith("/api/"):
            return await handler(request)
        if not _same_origin(request):
            return web.json_response({"error": "bad_origin"}, status=403)

        # Recorded whether or not it is allowed to authenticate, so an approval
        # can say which tailnet user answered it.
        identity = tailscale.identity(request.headers)
        request["tailscale_identity"] = identity

        # Read the token per request rather than closing over it, so rotating
        # it from the desktop app cuts clients off immediately.
        if secrets.compare_digest(_request_token(request), config["token"]):
            return await handler(request)
        if identity and _identity_may_authenticate(request, config):
            return await handler(request)
        return web.json_response({"error": "unauthorized"}, status=401)

    return middleware


@web.middleware
async def _no_stale_assets(request: web.Request, handler):
    """Make the client revalidate our own HTML, CSS and JS on every load.

    aiohttp's static handler sends ETag and Last-Modified but no Cache-Control,
    and a response with neither a max-age nor an expiry is cached *heuristically*:
    the client picks its own freshness window, conventionally a tenth of the
    asset's age. An app.js last written eight hours ago is therefore treated as
    fresh for roughly the next forty-five minutes, and a pane that loaded it in
    that window keeps using it -- through an edit, and through a restart of this
    process, because the cache is on the client's disk and not in ours.

    `no-cache` does not mean "do not store": it means "ask first". The ETag is
    still sent, so an unchanged asset comes back as a 304 with no body. Over
    loopback that costs nothing worth measuring, and it is the difference
    between shipping a fix and being told the fix did not arrive.
    """
    response = await handler(request)
    if not request.path.startswith("/api/") and request.path != "/api/ws":
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _build_app(manager: SessionManager, runtime: SdkRuntime, hub: ClientHub,
               config: dict) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware(config), _no_stale_assets])

    async def get_prefs(req):
        """What the transcript page should show in its footer.

        Read per request rather than closed over, so a change made in the
        desktop Settings pane reaches a browser on its next load without
        restarting the server.
        """
        return web.json_response(agent_options.display_prefs(config))

    async def check_auth(req):
        # Reached only with a valid token, so the client can use this to
        # tell "wrong token" apart from "no server there".
        return web.json_response({"ok": True})

    async def list_sessions(req):
        return web.json_response(manager.snapshot())

    async def get_session(req):
        sid = req.match_info["id"]
        for s in manager.snapshot():
            if s["id"] == sid:
                return web.json_response(s)
        raise web.HTTPNotFound()

    async def get_output(req):
        sid = req.match_info["id"]
        if manager.get(sid) is None:
            raise web.HTTPNotFound()
        return web.json_response({"html": manager.get_output(sid)})

    async def inject_session(req):
        sid = req.match_info["id"]
        if manager.get(sid) is None:
            raise web.HTTPNotFound()
        body = await req.json()
        text = body.get("text", "").strip()
        if text:
            manager.queue_inject(sid, text)
        return web.json_response({"ok": True})

    async def search_history(req):
        """Full-text search across every stored transcript (ISSUE-015).

        A scan rather than an index: 65 transcripts and 131 MiB answer in
        155-485 ms, which is inside the range where a search box feels
        immediate. See history_search.py for when that stops being true.
        """
        query = req.query.get("q", "")
        under = req.query.get("under") or None
        try:
            limit = min(int(req.query.get("limit", history_search.MAX_SESSIONS)),
                        history_search.MAX_SESSIONS)
        except ValueError:
            limit = history_search.MAX_SESSIONS
        hits = history_search.search(query, under=under, limit=limit)
        return web.json_response({
            "query": query,
            "hits": [h.to_dict() for h in hits],
        })

    async def get_history(req):
        """One stored transcript as events, for reading without resuming it.

        Keyed by the CLI's own session uuid rather than a Codinian session id,
        because the point is to open a conversation that has no live session
        and may never get one.
        """
        sdk_session_id = req.match_info["sdk_id"]
        if claude_history.transcript_path(sdk_session_id) is None:
            raise web.HTTPNotFound()
        events = claude_history.events_for_session(sdk_session_id)
        session = None
        for prior in claude_history.list_prior_sessions(limit=1000):
            if prior.id == sdk_session_id:
                session = prior
                break
        return web.json_response({
            "session_id": sdk_session_id,
            "cwd": session.cwd if session else "",
            "title": session.title if session else "",
            "mtime": session.mtime.isoformat() if session else None,
            "events": [{"type": etype, **data} for etype, data in events],
        })

    async def get_commands(req):
        """What a session can be asked to do (ISSUE-016).

        In the CLI this affordance is typing `/` and reading the list. A GUI
        has to build it, or the features are invisible: a user has to already
        know a skill exists to invoke it.
        """
        sid = req.match_info["id"]
        if manager.get(sid) is None:
            raise web.HTTPNotFound()
        commands = runtime.commands(sid) if runtime is not None else None
        if commands is None:
            # A terminal session, or one whose runtime never answered. An empty
            # list rather than an error: "this session has none" is a real
            # state and the client shows it as one.
            commands = []
        return web.json_response({"commands": commands})

    async def resume_history(req):
        """Open a stored conversation as a live session (ISSUE-015).

        Deliberately separate from the project route rather than sharing it.
        That one refuses any id the project does not own, which is the right
        guard when a project id is in the URL; here the whole point is that a
        search hit can live in a folder that was never registered as a project.
        The other guards are the same, and matter as much.
        """
        if runtime is None:
            return web.json_response({"error": "no_runtime"}, status=503)
        sdk_session_id = req.match_info["sdk_id"]
        prior = next((p for p in claude_history.list_prior_sessions(limit=1000)
                      if p.id == sdk_session_id), None)
        if prior is None:
            raise web.HTTPNotFound()

        existing = next((m for m in manager.sessions_meta()
                         if m.get("sdk_session_id") == sdk_session_id), None)
        if existing is not None:
            # Two live sessions appending to one conversation is not what
            # clicking Resume asked for.
            return web.json_response({"session": existing, "existing": True})

        workdir = prior.cwd if Path(prior.cwd).is_dir() else str(Path.home())
        # The mode the conversation itself was last on comes first: resuming
        # continues it, and a project default is a statement about new sessions.
        mode = claude_history.last_permission_mode(sdk_session_id)
        if mode not in PERMISSION_MODES:
            mode = project.settings_override(workdir, "default_permission_mode")
        if mode is None:
            mode = agent_options.default_permission_mode(config)
        if mode not in PERMISSION_MODES:
            return web.json_response({"error": "invalid_permission_mode"}, status=422)

        # A folder name for now: seeding the history renames it to the title
        # the conversation already had.
        session = Session(name=Path(workdir).name or "session",
                          workdir=workdir, kind="sdk", permission_mode=mode,
                          resume_session_id=sdk_session_id)
        manager.add(session)
        try:
            await runtime.create(session.id, session.workdir, session.permission_mode,
                                 resume=sdk_session_id)
        except Exception as exc:
            return web.json_response({"error": "start_failed", "message": str(exc)},
                                     status=500)
        return web.json_response({"session": manager.meta(session.id)})

    async def get_subagent(req):
        """One subagent's transcript, fetched when its card is expanded.

        Not seeded with the rest of the history on purpose: a subagent's
        transcript routinely runs larger than the parent it belongs to
        (measured at 7.2 MiB against a 9.4 MiB parent), so seeding every one
        would bury the conversation and re-send on every reconnect (ISSUE-017).
        """
        session = manager.get(req.match_info["id"])
        if session is None or not session.sdk_session_id:
            raise web.HTTPNotFound()
        events = claude_history.subagent_events(session.sdk_session_id,
                                                req.match_info["agent_id"])
        if not events:
            # Either no such subagent, or a transcript we cannot read. The
            # client shows "nothing recorded" rather than an error, because a
            # subagent that was launched and produced nothing is a real state.
            return web.json_response({"events": []})
        return web.json_response({
            "events": [{"type": etype, **data} for etype, data in events],
        })

    async def websocket(req):
        identity = req.get("tailscale_identity")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(req)
        hub.add(ws)
        await ws.send_json({"t": "sessions", "sessions": manager.sessions_meta()})
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                try:
                    await _handle_client_message(ws, data, manager, runtime, hub,
                                                 identity)
                except Exception:
                    traceback.print_exc()
                    await ws.send_json({"t": "error", "error": "request_failed",
                                        "request": data.get("t")})
        finally:
            hub.remove(ws)
        return ws

    async def index(req):
        return web.FileResponse(STATIC / "index.html")

    app.router.add_get("/api/auth", check_auth)
    app.router.add_get("/api/prefs", get_prefs)
    app.router.add_get("/api/sessions", list_sessions)
    app.router.add_get("/api/sessions/{id}", get_session)
    app.router.add_get("/api/sessions/{id}/output", get_output)
    app.router.add_post("/api/sessions/{id}/inject", inject_session)
    app.router.add_get("/api/sessions/{id}/commands", get_commands)
    app.router.add_get("/api/sessions/{id}/subagents/{agent_id}", get_subagent)
    # Stored transcripts, which belong to no live session (ISSUE-015).
    app.router.add_get("/api/history/search", search_history)
    app.router.add_get("/api/history/{sdk_id}", get_history)
    app.router.add_post("/api/history/{sdk_id}/resume", resume_history)
    app.router.add_get("/api/ws", websocket)
    # The project workspace routes (ISSUE-019, ISSUE-020). Registered before
    # the static handler so they are matched first, and inside the same
    # middleware, so the token and same-origin rules cover them too.
    projects_api.add_routes(app, manager, config, runtime)
    app.router.add_get("/", index)
    app.router.add_static("/", STATIC, show_index=False)
    return app


async def _handle_client_message(ws, data, manager, runtime, hub,
                                 identity: dict | None = None) -> None:
    t = data.get("t")
    if t == "subscribe":
        sid = data.get("session_id")
        if manager.get(sid) is None:
            return
        hub.subscribe(ws, sid)
        meta = manager.meta(sid)
        await ws.send_json({
            "t": "snapshot",
            "session_id": sid,
            "session": meta,
            "status": meta["status"] if meta else None,
            "events": manager.get_events(sid),
        })
    elif t == "unsubscribe":
        hub.unsubscribe(ws, data.get("session_id"))
    elif t == "subscribe_inbox":
        hub.subscribe_inbox(ws)
        approvals = []
        for approval in runtime.pending_approvals():
            approvals.append({**approval, "session": manager.meta(approval["session_id"])})
        await ws.send_json({"t": "inbox", "approvals": approvals})
    elif t == "unsubscribe_inbox":
        hub.unsubscribe_inbox(ws)
    elif t == "set_permission_mode":
        mode = data.get("mode")
        if mode not in PERMISSION_MODES:
            await ws.send_json({"t": "error", "error": "unknown_permission_mode",
                                "mode": mode})
        elif not runtime.set_permission_mode(data.get("session_id"), mode):
            await ws.send_json({"t": "error", "error": "unknown_session",
                                "session_id": data.get("session_id")})
    elif t == "send":
        # A refusal is worth a reply, unlike interrupt below: the message is
        # something the user wrote and expects to see land, and a terminal
        # session or a session whose turn loop has died would otherwise take it
        # and do nothing (ISSUE-036, ISSUE-038).
        if not runtime.send(data.get("session_id"), data.get("text", "")):
            await ws.send_json({"t": "error", "error": "session_not_running",
                                "session_id": data.get("session_id")})
    elif t == "interrupt":
        # No error reply on an unknown session: by the time a stop click lands,
        # the turn it meant to stop may already have finished, and telling the
        # user their stop failed would be worse than saying nothing.
        runtime.interrupt(data.get("session_id"))
    elif t == "resolve":
        ok = runtime.resolve(
            data.get("session_id"),
            data.get("request_id"),
            data.get("decision"),
            data.get("updated_input"),
            data.get("reason"),
            decided_by=(identity or {}).get("login"),
        )
        if not ok:
            await ws.send_json({"t": "error", "error": "stale_or_unknown_request",
                                "request_id": data.get("request_id")})
    elif t == "close":
        session_id = data.get("session_id")
        if manager.get(session_id) is None:
            # Its own error rather than `unknown_session`, which the browser
            # client routes to the permission-mode control.
            await ws.send_json({"t": "error", "error": "close_failed",
                                "session_id": session_id})
        else:
            # The runtime first: it owns the `claude` subprocess, and dropping
            # the session from the manager before disconnecting would leave
            # that process running with nothing left pointing at it. A terminal
            # session is not in the runtime at all, so False is an answer, not
            # a failure.
            await runtime.close(session_id)
            manager.remove(session_id)
    elif t == "rename":
        if not manager.rename(data.get("session_id"), data.get("name") or ""):
            await ws.send_json({"t": "error", "error": "rename_failed",
                                "session_id": data.get("session_id")})
    elif t == "create":
        session = Session(
            # A name the client sent is the user's, and sticks; without one the
            # session opens under its folder and takes the CLI's generated
            # title as soon as there is one (claude_history.generated_name).
            name=data.get("name") or Path(data.get("workdir") or "").name or "session",
            name_is_custom=bool(data.get("name")),
            workdir=data.get("workdir") or str(Path.home()),
            kind="sdk",
            permission_mode=data.get("permission_mode", "default"),
            resume_session_id=data.get("resume"),
        )
        manager.add(session)
        try:
            await runtime.create(session.id, session.workdir, session.permission_mode,
                                 resume=session.resume_session_id,
                                 initial_prompt=data.get("text"))
        except Exception as exc:
            # The session already emitted its own error event and status on the
            # bus; tell the caller directly too. Raising here would take the
            # WebSocket down with it.
            await ws.send_json({"t": "error", "error": "session_start_failed",
                                "session_id": session.id, "detail": str(exc)})


async def run_server(manager: SessionManager, runtime: SdkRuntime, config: dict) -> None:
    loop = asyncio.get_running_loop()
    runtime.bind_loop(loop)
    hub = ClientHub(loop, manager)

    # Wire the event bus and session-list changes to connected clients.
    manager.subscribe_events(hub.broadcast_event)
    manager.on_change(
        lambda: loop.call_soon_threadsafe(
            lambda: hub.broadcast_sessions(manager.sessions_meta())
        )
    )

    app = _build_app(manager, runtime, hub, config)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    bind, port = config["bind"], config["port"]
    site = web.TCPSite(runner, bind, port)
    await site.start()
    print(f"Codinian remote: http://{bind}:{port} (token required)")
    await asyncio.Event().wait()