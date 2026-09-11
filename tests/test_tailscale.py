"""Tailscale status, serve discovery and identity headers.

The subprocess calls are stubbed here because what matters is the parsing, not
that the `tailscale` binary works: the shapes below are the ones measured off a
real node and recorded in the module docstring.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from codinian import tailscale

STATUS_JSON = {
    "BackendState": "Running",
    "MagicDNSSuffix": "kangaroo-little.ts.net",
    "Self": {"DNSName": "latitude-kde.kangaroo-little.ts.net."},
}

SERVE_JSON = {
    "Web": {
        "latitude-kde.kangaroo-little.ts.net:443": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:8787"}}
        }
    }
}

PROXIED_HEADERS = {
    "Tailscale-User-Login": "someone@github",
    "Tailscale-User-Name": "someone",
    "Tailscale-User-Profile-Pic": "https://example.invalid/pic.png",
    "Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers",
    "X-Forwarded-Proto": "https",
}


@pytest.fixture(autouse=True)
def no_cache():
    """The module caches for 15 seconds, which would carry one test's stubbed
    answer into the next."""
    tailscale._cache.clear()
    yield
    tailscale._cache.clear()


@pytest.fixture
def stub_tailscale(monkeypatch):
    """Replace the `tailscale` binary with a recorded answer per subcommand."""
    def _stub(answers, returncode=0):
        def fake_run(args, **kwargs):
            key = " ".join(args[1:3])
            payload = answers.get(key)
            return subprocess.CompletedProcess(
                args, returncode,
                stdout="" if payload is None else json.dumps(payload), stderr="")
        monkeypatch.setattr(tailscale.subprocess, "run", fake_run)
    return _stub


# ------------------------------------------------------------------ status

def test_status_reports_the_magicdns_name_without_its_trailing_dot(stub_tailscale):
    stub_tailscale({"status --json": STATUS_JSON})
    assert tailscale.status() == {
        "running": True,
        "dns_name": "latitude-kde.kangaroo-little.ts.net",
        "tailnet": "kangaroo-little.ts.net",
    }


def test_a_stopped_backend_reads_as_not_running(stub_tailscale):
    stub_tailscale({"status --json": {**STATUS_JSON, "BackendState": "Stopped"}})
    assert tailscale.status()["running"] is False


@pytest.mark.parametrize("failure", [
    {"answers": {}, "returncode": 1},                    # command failed
    {"answers": {"status --json": "not a dict"}},        # unexpected shape
    {"answers": {"status --json": None}},                # no output
])
def test_every_way_of_not_knowing_gives_the_same_answer(stub_tailscale, failure):
    stub_tailscale(failure["answers"], failure.get("returncode", 0))
    assert tailscale.status() == {"running": False, "dns_name": None, "tailnet": None}


def test_a_missing_tailscale_binary_is_not_an_error(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("no tailscale here")
    monkeypatch.setattr(tailscale.subprocess, "run", missing)
    assert tailscale.status()["running"] is False
    assert tailscale.serve_target(8787) is None


def test_a_hung_command_is_not_an_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=4.0)
    monkeypatch.setattr(tailscale.subprocess, "run", timeout)
    assert tailscale.status()["running"] is False


def test_an_answer_is_cached_rather_than_re_run(stub_tailscale, monkeypatch):
    calls = []
    stub_tailscale({"status --json": STATUS_JSON})
    original = tailscale.subprocess.run
    monkeypatch.setattr(tailscale.subprocess, "run",
                        lambda *a, **k: (calls.append(a), original(*a, **k))[1])

    tailscale.status()
    tailscale.status()
    assert len(calls) == 1


# ------------------------------------------------------------ serve target

def test_serve_target_finds_the_url_proxying_this_port(stub_tailscale):
    stub_tailscale({"serve status": SERVE_JSON})
    assert tailscale.serve_target(8787) == {
        "url": "https://latitude-kde.kangaroo-little.ts.net",
        "host": "latitude-kde.kangaroo-little.ts.net",
        "path": "/",
    }


def test_a_different_port_is_not_this_port(stub_tailscale):
    stub_tailscale({"serve status": SERVE_JSON})
    assert tailscale.serve_target(9999) is None


def test_a_non_standard_listen_port_appears_in_the_url(stub_tailscale):
    stub_tailscale({"serve status": {"Web": {"host.ts.net:8443": {
        "Handlers": {"/": {"Proxy": "http://127.0.0.1:8787"}}}}}})
    assert tailscale.serve_target(8787)["url"] == "https://host.ts.net:8443"


def test_a_sub_path_is_kept(stub_tailscale):
    stub_tailscale({"serve status": {"Web": {"host.ts.net:443": {
        "Handlers": {"/codinian": {"Proxy": "http://127.0.0.1:8787/"}}}}}})
    target = tailscale.serve_target(8787)
    assert target["url"] == "https://host.ts.net/codinian"
    assert target["path"] == "/codinian"


def test_nothing_served_at_all_is_none(stub_tailscale):
    stub_tailscale({"serve status": {"Web": {}}})
    assert tailscale.serve_target(8787) is None


# --------------------------------------------------------------- identity

def test_a_proxied_request_yields_the_tailnet_user():
    assert tailscale.identity(PROXIED_HEADERS) == {
        "login": "someone@github",
        "name": "someone",
        "profile_pic": "https://example.invalid/pic.png",
    }


def test_a_login_header_alone_is_not_enough():
    # Any local process can set a header. Without the markers `tailscale serve`
    # adds, this is not even consistent with a proxied request.
    assert tailscale.identity({"Tailscale-User-Login": "someone@github"}) is None


def test_either_marker_header_is_accepted():
    for marker in ({"Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers"},
                   {"X-Forwarded-Proto": "https"}):
        assert tailscale.identity({"Tailscale-User-Login": "x@y", **marker}) is not None


def test_a_forwarded_proto_that_is_not_https_is_not_a_marker():
    assert tailscale.identity({"Tailscale-User-Login": "x@y",
                               "X-Forwarded-Proto": "http"}) is None


def test_no_login_header_means_no_identity():
    assert tailscale.identity({}) is None
    assert tailscale.identity(PROXIED_HEADERS | {"Tailscale-User-Login": ""}) is None


def test_the_login_stands_in_for_a_missing_display_name():
    headers = dict(PROXIED_HEADERS)
    del headers["Tailscale-User-Name"]
    assert tailscale.identity(headers)["name"] == "someone@github"
