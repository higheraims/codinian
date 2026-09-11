"""The remote server's authentication middleware.

Approving a tool call over this socket runs code on this machine, and the
server can be reachable over a tailnet, so this is the boundary worth testing
directly rather than through a route.

The middleware is exercised against a real aiohttp application carrying nothing
but itself and one handler. Building the whole app would drag in the SDK
runtime and the session manager for no added coverage of the thing under test.
"""

from __future__ import annotations

import asyncio
import functools

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from codinian import config as config_module
from codinian import tailscale
from codinian.remote import server

TOKEN = "a-real-token-value"


def async_test(fn):
    """Run a coroutine test body. The suite has no asyncio plugin installed,
    and one function is cheaper than a dependency."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def make_config(**overrides) -> dict:
    return {**config_module.DEFAULTS, "token": TOKEN, "port": 8787, **overrides}


async def client_for(config: dict) -> TestClient:
    app = web.Application(middlewares=[server._auth_middleware(config)])

    async def ok(request):
        return web.json_response({"ok": True,
                                  "identity": request.get("tailscale_identity")})

    app.router.add_get("/api/ping", ok)
    app.router.add_get("/index.html", ok)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ------------------------------------------------------------ token forms

@async_test
async def test_a_bearer_token_is_accepted():
    client = await client_for(make_config())
    try:
        resp = await client.get("/api/ping", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status == 200
    finally:
        await client.close()


@async_test
async def test_the_dedicated_header_is_accepted():
    client = await client_for(make_config())
    try:
        resp = await client.get("/api/ping", headers={"X-Codinian-Token": TOKEN})
        assert resp.status == 200
    finally:
        await client.close()


@async_test
async def test_a_query_parameter_is_accepted_because_websockets_cannot_send_headers():
    client = await client_for(make_config())
    try:
        resp = await client.get(f"/api/ping?token={TOKEN}")
        assert resp.status == 200
    finally:
        await client.close()


@async_test
async def test_no_token_is_refused():
    client = await client_for(make_config())
    try:
        resp = await client.get("/api/ping")
        assert resp.status == 401
        assert (await resp.json()) == {"error": "unauthorized"}
    finally:
        await client.close()


@pytest.mark.parametrize("presented", [
    "wrong-token",
    TOKEN[:-1],
    TOKEN + "x",
    TOKEN.upper(),
    "",
])
def test_a_token_that_is_not_the_token_is_refused(presented):
    @async_test
    async def body():
        client = await client_for(make_config())
        try:
            resp = await client.get("/api/ping",
                                    headers={"X-Codinian-Token": presented})
            assert resp.status == 401
        finally:
            await client.close()
    body()


@async_test
async def test_a_rotated_token_cuts_a_client_off_at_its_next_request():
    config = make_config()
    client = await client_for(config)
    try:
        assert (await client.get("/api/ping", headers={"X-Codinian-Token": TOKEN})).status == 200
        # Read per request rather than closed over, which is what makes
        # rotating from the desktop app immediate.
        config["token"] = "rotated-token"
        assert (await client.get("/api/ping", headers={"X-Codinian-Token": TOKEN})).status == 401
        assert (await client.get("/api/ping",
                                 headers={"X-Codinian-Token": "rotated-token"})).status == 200
    finally:
        await client.close()


@async_test
async def test_static_assets_need_no_token():
    # The page has to load before it can ask for a token, and it carries no
    # session data.
    client = await client_for(make_config())
    try:
        assert (await client.get("/index.html")).status == 200
    finally:
        await client.close()


# ------------------------------------------------------------ same origin

@async_test
async def test_a_cross_origin_request_is_refused_before_the_token_is_read():
    client = await client_for(make_config())
    try:
        resp = await client.get("/api/ping",
                                headers={"Authorization": f"Bearer {TOKEN}",
                                         "Origin": "https://evil.example"})
        assert resp.status == 403
        assert (await resp.json()) == {"error": "bad_origin"}
    finally:
        await client.close()


def test_a_request_with_no_origin_header_passes_the_check():
    # curl, the desktop pane's own fetches, and every non-browser client.
    request = make_mocked_request("GET", "/api/ping", headers={"Host": "127.0.0.1:8787"})
    assert server._same_origin(request) is True


def test_an_origin_matching_the_host_passes():
    request = make_mocked_request("GET", "/api/ping", headers={
        "Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787"})
    assert server._same_origin(request) is True


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:9999",
    "http://evil.example",
    "https://127.0.0.1:8787.evil.example",
    "null",
])
def test_an_origin_that_is_not_the_host_fails(origin):
    request = make_mocked_request("GET", "/api/ping", headers={
        "Host": "127.0.0.1:8787", "Origin": origin})
    assert server._same_origin(request) is False


# ---------------------------------------------------- token extraction

@pytest.mark.parametrize("headers, query, expected", [
    ({"Authorization": "Bearer abc"}, "", "abc"),
    ({"Authorization": "Bearer  abc  "}, "", "abc"),
    ({"Authorization": "Basic abc"}, "", ""),
    ({"X-Codinian-Token": "abc"}, "", "abc"),
    ({}, "?token=abc", "abc"),
    ({}, "", ""),
    # The Authorization header wins over the query string.
    ({"Authorization": "Bearer header-one"}, "?token=query-one", "header-one"),
])
def test_the_token_is_read_from_the_expected_places(headers, query, expected):
    request = make_mocked_request("GET", f"/api/ping{query}", headers=headers)
    assert server._request_token(request) == expected


# ------------------------------------------------- tailscale identity

PROXIED = {
    "Tailscale-User-Login": "someone@github",
    "Tailscale-User-Name": "someone",
    "Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers",
}


@pytest.fixture
def serve_is_proxying(monkeypatch):
    monkeypatch.setattr(tailscale, "serve_target",
                        lambda port: {"url": "https://host.ts.net", "host": "host.ts.net",
                                      "path": "/"})


@pytest.fixture
def serve_is_not_proxying(monkeypatch):
    monkeypatch.setattr(tailscale, "serve_target", lambda port: None)


def test_identity_is_recorded_even_when_it_cannot_authenticate(serve_is_not_proxying):
    @async_test
    async def body():
        client = await client_for(make_config())
        try:
            resp = await client.get("/api/ping", headers={
                "X-Codinian-Token": TOKEN, **PROXIED})
            # Recorded so an approval can say which tailnet user answered it.
            assert (await resp.json())["identity"]["login"] == "someone@github"
        finally:
            await client.close()
    body()


def test_identity_alone_is_refused_while_the_setting_is_off(serve_is_proxying):
    @async_test
    async def body():
        client = await client_for(make_config(trust_tailscale_identity=False))
        try:
            assert (await client.get("/api/ping", headers=PROXIED)).status == 401
        finally:
            await client.close()
    body()


def test_identity_authenticates_only_with_every_condition_met(serve_is_proxying):
    @async_test
    async def body():
        client = await client_for(make_config(trust_tailscale_identity=True))
        try:
            assert (await client.get("/api/ping", headers=PROXIED)).status == 200
        finally:
            await client.close()
    body()


def test_identity_is_refused_on_an_open_bind(serve_is_proxying):
    @async_test
    async def body():
        # On an open bind, anything on the network could send the header.
        client = await client_for(make_config(trust_tailscale_identity=True,
                                              bind=config_module.ALL_INTERFACES))
        try:
            assert (await client.get("/api/ping", headers=PROXIED)).status == 401
        finally:
            await client.close()
    body()


def test_identity_is_refused_when_serve_is_not_proxying_this_port(serve_is_not_proxying):
    @async_test
    async def body():
        client = await client_for(make_config(trust_tailscale_identity=True))
        try:
            assert (await client.get("/api/ping", headers=PROXIED)).status == 401
        finally:
            await client.close()
    body()


def test_identity_is_refused_from_a_non_loopback_peer(serve_is_proxying):
    config = make_config(trust_tailscale_identity=True)
    request = make_mocked_request("GET", "/api/ping", headers=PROXIED)
    # make_mocked_request has no peer address, so request.remote is None.
    assert server._identity_may_authenticate(request, config) is False


def test_a_bare_login_header_does_not_become_an_identity(serve_is_proxying):
    @async_test
    async def body():
        client = await client_for(make_config(trust_tailscale_identity=True))
        try:
            # No marker header, so tailscale.identity() returns None and there
            # is nothing to promote.
            resp = await client.get("/api/ping",
                                    headers={"Tailscale-User-Login": "someone@github"})
            assert resp.status == 401
        finally:
            await client.close()
    body()


# ------------------------------------------------------------ caching

@async_test
async def test_our_own_assets_are_marked_no_cache():
    app = web.Application(middlewares=[server._no_stale_assets])

    async def ok(request):
        return web.Response(text="body")

    app.router.add_get("/app.js", ok)
    app.router.add_get("/api/thing", ok)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        # "no-cache" means "ask first", not "do not store": the ETag still
        # answers 304. Without it a stale app.js survives a restart.
        assert (await client.get("/app.js")).headers["Cache-Control"] == "no-cache"
        assert "Cache-Control" not in (await client.get("/api/thing")).headers
    finally:
        await client.close()
