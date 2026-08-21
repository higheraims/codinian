"""HTTP routes for the project workspace (ISSUE-019, ISSUE-020).

The contract is docs/project-workspace-protocol.md. These are plain REST
routes rather than WebSocket messages because nothing here is pushed: a
project changes when the user changes it, so the client asks and the server
answers. They live under /api/ like everything else, which means the token,
the same-origin check and the Tailscale identity rules in server.py already
apply to every one of them.

The modules underneath (project, files, vcs, issues) know nothing about HTTP
and nothing about each other. This file is the only place the four are
combined, which keeps the path-safety boundary in files.resolve_in_project
rather than scattered through route handlers.

One route here does push state around rather than just reading it: resuming a
prior `claude` session (ISSUE-024) starts a real SDK session, which is why
add_routes takes the runtime. It stays REST for the same reason as the rest of
this file -- the client asks for it, nothing pushes it -- and the session it
creates then behaves like any other, events and all.
"""

from __future__ import annotations

import datetime
import functools
import json
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

import claude_history
import files
import issues as issues_mod
import project as project_mod
import vcs
from session import PERMISSION_MODES, Session

# Answered to the client as {"error": code, "detail": ...}. Kept in one place
# because the browser maps codes to messages and a typo here is a message the
# user never sees.
ERR_BAD_PATH = "bad_path"
ERR_NOT_FOUND = "not_found"
ERR_INVALID = "invalid"
ERR_EXISTS = "exists"
ERR_NOT_A_REPO = "not_a_repo"
ERR_GIT_FAILED = "git_failed"
ERR_NO_RUNTIME = "no_runtime"
ERR_START_FAILED = "session_start_failed"


def _iso_dates(value):
    """JSON encoder fallback for the `date` objects issues.py hands back.

    Issue front matter carries real dates, not strings, deliberately: that is
    what lets the serializer tell a date to write bare from a string that
    merely looks like one and must be quoted. JSON has no date type, so the
    wire form is ISO 8601 and the client sends the same back.
    """
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


_issue_json = functools.partial(json.dumps, default=_iso_dates)


def _err(code: str, status: int, detail: str = "") -> web.Response:
    return web.json_response({"error": code, "detail": detail}, status=status)


def _project_or_404(pid: str) -> project_mod.Project:
    proj = project_mod.get_project(pid)
    if proj is None:
        raise web.HTTPNotFound(text='{"error": "not_found", "detail": "no such project"}',
                               content_type="application/json")
    return proj


def _root(proj: project_mod.Project) -> Path:
    return Path(proj.path)


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _history(root: Path, manager) -> list[dict]:
    """Every `claude` session ever run inside this project, whether Codinian
    started it or not (ISSUE-024).

    Codinian keeps live sessions in memory only, and the CLI has its own
    history besides, so before this the Sessions tab showed nothing for a repo
    the user had been working in all week. The durable record is the SDK's own
    JSONL under ~/.claude/projects, so that is what gets read.

    `session_id` is the live session already carrying this conversation, or
    null when nothing has picked it up in this run. The client uses it to leave
    that entry out of the earlier-conversations list, since it is already
    showing as a running session.
    """
    live = {m["sdk_session_id"]: m["id"]
            for m in manager.sessions_meta() if m.get("sdk_session_id")}
    return [
        {
            "sdk_session_id": prior.id,
            "title": prior.title,
            "cwd": prior.cwd,
            "mtime": prior.mtime.isoformat(),
            "session_id": live.get(prior.id),
        }
        for prior in claude_history.list_prior_sessions(under=root)
    ]


def add_routes(app: web.Application, manager, config: dict, runtime=None) -> None:
    """Register every /api/projects route on an existing application.

    `runtime` is the SdkRuntime a resumed session starts on. Leaving it out
    still registers the route; it answers 503 and names what is missing, which
    beats a 404 that reads like the route was never built."""

    # ------------------------------------------------------------- projects

    async def list_projects(req):
        return web.json_response({"projects": [p.meta() for p in project_mod.load_registry()]})

    async def create_project(req):
        body = await _json_body(req)
        path = (body.get("path") or "").strip()
        if not path:
            return _err(ERR_INVALID, 422, "a path is required")
        try:
            proj = project_mod.add_project(path)
        except FileNotFoundError:
            return _err(ERR_NOT_FOUND, 404, f"{path} is not a directory")
        return web.json_response({"project": proj.meta()})

    async def delete_project(req):
        # Registry only. Deregistering a project must never be a way to lose
        # the folder it points at.
        removed = project_mod.remove_project(req.match_info["id"])
        if not removed:
            return _err(ERR_NOT_FOUND, 404, "no such project")
        return web.json_response({"ok": True})

    async def get_project(req):
        proj = _project_or_404(req.match_info["id"])
        root = _root(proj)
        settings = project_mod.load_settings(root)
        git_info = vcs.info(root) if vcs.is_repo(root) else None
        pid = proj.id
        sessions = [m for m in manager.sessions_meta() if m.get("project_id") == pid]
        return web.json_response({
            "project": proj.meta(),
            "settings": settings,
            "git": git_info,
            "sessions": sessions,
            "history": _history(root, manager),
        })

    async def put_settings(req):
        proj = _project_or_404(req.match_info["id"])
        body = await _json_body(req)
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return _err(ERR_INVALID, 422, "settings must be an object")
        try:
            saved = project_mod.save_settings(_root(proj), settings)
        except ValueError as exc:
            return _err(ERR_INVALID, 422, str(exc))
        except OSError as exc:
            return _err(ERR_INVALID, 500, str(exc))
        return web.json_response({"settings": saved})

    # ---------------------------------------------------------------- files

    async def list_files(req):
        proj = _project_or_404(req.match_info["id"])
        root = _root(proj)
        rel = req.query.get("path", "")
        checker = (lambda paths: vcs.check_ignore(root, paths)) if vcs.is_repo(root) else None
        try:
            entries = files.list_dir(root, rel, ignored_checker=checker)
        except files.PathError as exc:
            return _err(ERR_BAD_PATH, 400, str(exc))
        except FileNotFoundError:
            return _err(ERR_NOT_FOUND, 404, rel)
        except NotADirectoryError:
            return _err(ERR_INVALID, 422, f"{rel} is not a directory")
        return web.json_response({"path": rel, "entries": entries})

    async def get_file(req):
        proj = _project_or_404(req.match_info["id"])
        rel = req.query.get("path", "")
        try:
            return web.json_response(files.read_text_file(_root(proj), rel))
        except files.PathError as exc:
            return _err(ERR_BAD_PATH, 400, str(exc))
        except files.FileTooLarge as exc:
            return web.json_response({"error": "too_large", "size": exc.size,
                                      "detail": "file is too large to open here"},
                                     status=413)
        except files.BinaryFile as exc:
            return web.json_response({"error": "binary", "size": exc.size,
                                      "detail": "not a text file"}, status=415)
        except (FileNotFoundError, IsADirectoryError):
            return _err(ERR_NOT_FOUND, 404, rel)

    async def put_file(req):
        proj = _project_or_404(req.match_info["id"])
        body = await _json_body(req)
        rel, content = body.get("path", ""), body.get("content")
        if not isinstance(content, str):
            return _err(ERR_INVALID, 422, "content must be a string")
        try:
            return web.json_response(files.write_text_file(_root(proj), rel, content))
        except files.PathError as exc:
            return _err(ERR_BAD_PATH, 400, str(exc))
        except (FileNotFoundError, IsADirectoryError) as exc:
            return _err(ERR_NOT_FOUND, 404, str(exc))

    async def new_entry(req):
        proj = _project_or_404(req.match_info["id"])
        body = await _json_body(req)
        rel, kind = body.get("path", ""), body.get("kind", "file")
        if kind not in ("file", "dir"):
            return _err(ERR_INVALID, 422, "kind must be file or dir")
        try:
            return web.json_response(files.create_entry(_root(proj), rel, kind))
        except files.PathError as exc:
            return _err(ERR_BAD_PATH, 400, str(exc))
        except FileExistsError:
            return _err(ERR_EXISTS, 409, rel)
        except FileNotFoundError as exc:
            return _err(ERR_NOT_FOUND, 404, str(exc))

    async def rename_entry(req):
        proj = _project_or_404(req.match_info["id"])
        body = await _json_body(req)
        rel, to = body.get("path", ""), body.get("to", "")
        try:
            return web.json_response(files.rename_entry(_root(proj), rel, to))
        except files.PathError as exc:
            return _err(ERR_BAD_PATH, 400, str(exc))
        except FileExistsError:
            return _err(ERR_EXISTS, 409, to)
        except FileNotFoundError:
            return _err(ERR_NOT_FOUND, 404, rel)

    async def open_external(req):
        proj = _project_or_404(req.match_info["id"])
        if not _is_desktop_request(req):
            return _err("not_local", 403,
                        "opening a file in a desktop application only works "
                        "from the app on this machine")
        body = await _json_body(req)
        try:
            files.open_external(_root(proj), body.get("path", ""))
        except files.PathError as exc:
            return _err(ERR_BAD_PATH, 400, str(exc))
        except FileNotFoundError:
            return _err(ERR_NOT_FOUND, 404, body.get("path", ""))
        return web.json_response({"ok": True})

    # ------------------------------------------------------------------ git

    async def git_info(req):
        proj = _project_or_404(req.match_info["id"])
        root = _root(proj)
        if not vcs.is_repo(root):
            return web.json_response({"is_repo": False})
        return web.json_response(vcs.info(root))

    async def git_init(req):
        proj = _project_or_404(req.match_info["id"])
        root = _root(proj)
        if vcs.is_repo(root):
            return _err(ERR_EXISTS, 409, "already a git repository")
        try:
            return web.json_response(vcs.init(root))
        except vcs.GitError as exc:
            return _err(ERR_GIT_FAILED, 422, exc.stderr)

    async def git_commit(req):
        proj = _project_or_404(req.match_info["id"])
        root = _root(proj)
        if not vcs.is_repo(root):
            return _err(ERR_NOT_A_REPO, 409, "not a git repository")
        body = await _json_body(req)
        paths = body.get("paths")
        if paths is not None and not isinstance(paths, list):
            return _err(ERR_INVALID, 422, "paths must be a list or null")
        try:
            return web.json_response(vcs.commit(root, body.get("message", ""), paths))
        except ValueError as exc:
            return _err(ERR_INVALID, 422, str(exc))
        except vcs.GitError as exc:
            return _err(ERR_GIT_FAILED, 422, exc.stderr)

    async def git_tag(req):
        proj = _project_or_404(req.match_info["id"])
        root = _root(proj)
        if not vcs.is_repo(root):
            return _err(ERR_NOT_A_REPO, 409, "not a git repository")
        body = await _json_body(req)
        name = (body.get("name") or "").strip()
        if not name:
            return _err(ERR_INVALID, 422, "a tag name is required")
        try:
            return web.json_response(vcs.create_tag(root, name, body.get("message")))
        except vcs.GitError as exc:
            return _err(ERR_GIT_FAILED, 422, exc.stderr)

    async def get_gitignore(req):
        proj = _project_or_404(req.match_info["id"])
        return web.json_response(vcs.read_gitignore(_root(proj)))

    async def put_gitignore(req):
        proj = _project_or_404(req.match_info["id"])
        body = await _json_body(req)
        content = body.get("content")
        if not isinstance(content, str):
            return _err(ERR_INVALID, 422, "content must be a string")
        vcs.write_gitignore(_root(proj), content)
        return web.json_response({"ok": True})

    # --------------------------------------------------------------- issues

    def _issues_dir(proj) -> tuple[Path, str, dict]:
        root = _root(proj)
        settings = project_mod.load_settings(root)
        return root, project_mod.resolve_issues_dir(root, settings), settings

    async def list_issues(req):
        proj = _project_or_404(req.match_info["id"])
        root, issues_dir, settings = _issues_dir(proj)
        return web.json_response({
            "issues": issues_mod.list_issues(root, issues_dir),
            "schema": settings.get("issues", {}),
            "issues_dir": issues_dir,
        }, dumps=_issue_json)

    async def get_issue(req):
        proj = _project_or_404(req.match_info["id"])
        root, issues_dir, _ = _issues_dir(proj)
        issue = issues_mod.read_issue(root, issues_dir, req.match_info["issue"])
        if issue is None:
            return _err(ERR_NOT_FOUND, 404, req.match_info["issue"])
        return web.json_response({"issue": issue}, dumps=_issue_json)

    async def create_issue(req):
        proj = _project_or_404(req.match_info["id"])
        root, issues_dir, settings = _issues_dir(proj)
        body = await _json_body(req)
        frontmatter = body.get("frontmatter") or {}
        sections = body.get("sections") or []
        if not str(frontmatter.get("title", "")).strip():
            return _err(ERR_INVALID, 422, "an issue needs a title")
        prefix = settings.get("issues", {}).get("id_prefix", "ISSUE")
        try:
            issue = issues_mod.create_issue(root, issues_dir, frontmatter, sections,
                                            prefix=prefix)
        except OSError as exc:
            return _err(ERR_INVALID, 500, str(exc))
        return web.json_response({"issue": issue}, dumps=_issue_json)

    async def update_issue(req):
        proj = _project_or_404(req.match_info["id"])
        root, issues_dir, _ = _issues_dir(proj)
        body = await _json_body(req)
        issue = issues_mod.update_issue(root, issues_dir, req.match_info["issue"],
                                        body.get("frontmatter") or {},
                                        body.get("sections") or [])
        if issue is None:
            return _err(ERR_NOT_FOUND, 404, req.match_info["issue"])
        return web.json_response({"issue": issue}, dumps=_issue_json)

    # ------------------------------------------------------------- sessions

    async def create_session(req):
        """Resume a prior `claude` session, or start a fresh one, in this
        project. Answers with the SessionMeta the client should open."""
        proj = _project_or_404(req.match_info["id"])
        if runtime is None:
            return _err(ERR_NO_RUNTIME, 503, "this server has no session runtime")
        root = _root(proj)
        if not root.is_dir():
            return _err(ERR_NOT_FOUND, 404, f"{proj.path} is no longer there")

        body = await _json_body(req)
        resume = (body.get("resume") or "").strip() or None
        # Checked before anything is created, and before the already-open
        # shortcut below, so a bad mode is always answered as a bad mode.
        # For a resume with no mode asked for, the conversation's own last mode
        # wins over the project default: resuming continues a conversation, and
        # the default is a statement about starting a new one.
        mode = body.get("permission_mode")
        if not mode and resume:
            mode = claude_history.last_permission_mode(resume)
            if mode not in PERMISSION_MODES:
                mode = None
        if not mode:
            mode = project_mod.load_settings(root).get("default_permission_mode", "default")
        if mode not in PERMISSION_MODES:
            return _err(ERR_INVALID, 422, f"unknown permission mode: {mode}")
        workdir = str(root)

        if resume:
            prior = next((entry for entry in claude_history.list_prior_sessions(under=root)
                          if entry.id == resume), None)
            if prior is None:
                # Deliberately not "resume whatever id you name": the id has to
                # be one this project actually owns, so a client cannot use a
                # project route to reopen some other repo's conversation.
                return _err(ERR_NOT_FOUND, 404, "no such session in this project")
            existing = next((m for m in manager.sessions_meta()
                             if m.get("sdk_session_id") == resume), None)
            if existing is not None:
                # Already open. Two live sessions appending to one conversation
                # is not what clicking Resume asked for; hand back the one that
                # is already running.
                return web.json_response({"session": existing, "existing": True})
            if Path(prior.cwd).is_dir():
                workdir = prior.cwd

        session = Session(
            # Named after the folder only until the conversation names itself:
            # seeding a resume renames it at once, a new session at the end of
            # its first turn (claude_history.generated_name). A name sent by
            # the client is the user's, and stays.
            name=body.get("name") or Path(workdir).name or proj.name,
            name_is_custom=bool(body.get("name")),
            workdir=workdir,
            kind="sdk",
            permission_mode=mode,
            resume_session_id=resume,
        )
        manager.add(session)
        try:
            await runtime.create(session.id, session.workdir, session.permission_mode,
                                 resume=resume)
        except Exception as exc:
            # The session has already put its own error event and status on the
            # bus, so it stays in the list saying what went wrong rather than
            # vanishing. Say it here too, for a client that never subscribed.
            return _err(ERR_START_FAILED, 500, str(exc))
        return web.json_response({"session": manager.meta(session.id)})

    base = "/api/projects"
    app.router.add_get(base, list_projects)
    app.router.add_post(base, create_project)
    app.router.add_get(base + "/{id}", get_project)
    app.router.add_delete(base + "/{id}", delete_project)
    app.router.add_put(base + "/{id}/settings", put_settings)

    app.router.add_get(base + "/{id}/files", list_files)
    app.router.add_get(base + "/{id}/file", get_file)
    app.router.add_put(base + "/{id}/file", put_file)
    app.router.add_post(base + "/{id}/file/new", new_entry)
    app.router.add_post(base + "/{id}/file/rename", rename_entry)
    app.router.add_post(base + "/{id}/open-external", open_external)

    app.router.add_get(base + "/{id}/git", git_info)
    app.router.add_post(base + "/{id}/git/init", git_init)
    app.router.add_post(base + "/{id}/git/commit", git_commit)
    app.router.add_post(base + "/{id}/git/tag", git_tag)
    app.router.add_get(base + "/{id}/gitignore", get_gitignore)
    app.router.add_put(base + "/{id}/gitignore", put_gitignore)

    app.router.add_get(base + "/{id}/issues", list_issues)
    app.router.add_post(base + "/{id}/issues", create_issue)
    app.router.add_get(base + "/{id}/issues/{issue}", get_issue)
    app.router.add_put(base + "/{id}/issues/{issue}", update_issue)

    app.router.add_post(base + "/{id}/sessions", create_session)


def _is_desktop_request(req: web.Request) -> bool:
    """Whether this request plausibly came from the app on this machine, which
    is the only client for which "open this file in an editor" means anything.

    Loopback alone does not answer it. `tailscale serve` proxies from
    127.0.0.1, so a phone on the tailnet looks exactly like the desktop pane at
    the socket -- the same limitation documented for identity headers in
    server.py. Two further signals narrow it: a Tailscale identity header means
    the request definitely came through the proxy, and the desktop pane always
    addresses the server as 127.0.0.1, where a tailnet client addresses it by
    its magic-DNS name.

    A local process could forge all of this, but a local process can already
    run xdg-open. What this rules out is the remote case, which is the point.
    """
    if req.remote not in ("127.0.0.1", "::1"):
        return False
    if req.get("tailscale_identity"):
        return False
    host = (req.headers.get("Host") or "").split(":")[0]
    if host and host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
        return False
    origin = req.headers.get("Origin")
    if origin:
        origin_host = urlparse(origin).hostname
        if origin_host and origin_host not in ("127.0.0.1", "localhost", "::1"):
            return False
    return True
