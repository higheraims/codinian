"""The remote server's config file: defaults, the token, and the URL."""

from __future__ import annotations

import json
import os
import stat

import pytest

import config


@pytest.fixture(autouse=True)
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    return path


def test_first_run_generates_a_token_and_writes_the_file(config_file):
    loaded = config.load()
    assert loaded["token"]
    assert config_file.is_file()
    assert json.loads(config_file.read_text())["token"] == loaded["token"]


def test_the_config_file_is_not_readable_by_anyone_else(config_file):
    config.load()
    mode = stat.S_IMODE(os.stat(config_file).st_mode)
    assert mode == 0o600, f"token file is {oct(mode)}"


def test_a_generated_token_is_long_and_unpredictable():
    first, second = config.new_token(), config.new_token()
    assert first != second
    assert len(first) >= 40


def test_defaults_are_filled_in_around_what_the_file_says(config_file):
    config_file.write_text(json.dumps({"token": "kept", "port": 9999}))
    loaded = config.load()
    assert loaded["port"] == 9999
    assert loaded["token"] == "kept"
    assert loaded["bind"] == config.LOOPBACK
    assert loaded["theme"] == "system"


def test_the_bind_default_is_loopback(config_file):
    # Approving a tool call over this socket runs code on this machine.
    assert config.load()["bind"] == "127.0.0.1"
    assert config.DEFAULTS["bind"] == config.LOOPBACK


def test_tailscale_identity_is_off_by_default(config_file):
    assert config.load()["trust_tailscale_identity"] is False


@pytest.mark.parametrize("content", ["{not json", '["a list"]', ""])
def test_a_corrupt_config_is_replaced_rather_than_breaking_startup(config_file, content):
    config_file.write_text(content)
    loaded = config.load()
    assert loaded["token"]
    assert loaded["port"] == config.DEFAULTS["port"]


def test_an_existing_token_is_not_regenerated_on_every_load(config_file):
    first = config.load()["token"]
    assert config.load()["token"] == first


def test_rotate_token_replaces_and_persists(config_file):
    loaded = config.load()
    old = loaded["token"]
    rotated = config.rotate_token(loaded)

    assert rotated != old
    assert loaded["token"] == rotated
    assert json.loads(config_file.read_text())["token"] == rotated


def test_url_for_loopback_carries_the_token(config_file):
    loaded = config.load()
    loaded.update(port=8787, bind=config.LOOPBACK)
    assert config.url_for(loaded) == f"http://127.0.0.1:8787/?token={loaded['token']}"


def test_url_for_accepts_an_explicit_host(config_file):
    loaded = config.load()
    loaded.update(port=8787, bind=config.ALL_INTERFACES)
    assert config.url_for(loaded, "laptop.tailnet.ts.net").startswith(
        "http://laptop.tailnet.ts.net:8787/?token=")


def test_local_ip_returns_an_address_without_sending_anything():
    # The UDP connect picks a route; it never puts a packet on the wire.
    assert config.local_ip().count(".") == 3


def test_saving_leaves_no_temp_file_behind(config_file):
    config.load()
    assert [p.name for p in config_file.parent.iterdir()] == ["config.json"]
