"""Settings → Devices: Same Wi-Fi as a second listener that comes and goes at once, and the SSH hosts Add device offers."""
from __future__ import annotations

import asyncio
import os
import socket

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from openprogram.host.backend.ssh_config import config_hosts
from openprogram.interfaces.server import lan_listener


def test_ssh_hosts_come_from_the_config_and_its_includes_without_patterns(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ssh = home / ".ssh"
    (ssh / "conf.d").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (ssh / "config").write_text(
        "Include conf.d/*\n"
        "Host lab gpu-*\n  HostName 10.0.0.5\n"
        "Host *\n  ServerAliveInterval 30\n"
        "host=devbox\n"
        "# Host commented\n"
        "Host !nope lab\n",
        encoding="utf-8",
    )
    (ssh / "conf.d" / "work").write_text("Host office\n  User me\n", encoding="utf-8")
    assert config_hosts() == ["lab", "devbox", "office"]


def test_no_ssh_config_means_no_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_hosts() == []


class _Auth:
    def __init__(self, port: int):
        self.port = port
        self.effective_origins: set[str] = set()

    def allow_origins(self, added=(), removed=()):
        self.effective_origins = (self.effective_origins - set(removed)) | set(added)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def wifi(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    os.chmod(cfg, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: cfg)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: cfg)
    # The test's "local network" is loopback, so the listener is reachable here.
    monkeypatch.setattr("openprogram.host.security.remote_access.lan_address", lambda: "127.0.0.1")
    app = Starlette(routes=[Route("/ping", lambda request: PlainTextResponse("pong"))])
    yield app
    lan_listener._app = None


def test_same_wifi_listens_and_stops_at_once_and_its_origin_follows(wifi):
    async def scenario():
        auth = _Auth(_free_port())
        lan_listener.remember(True)
        origin = await lan_listener.apply(auth, wifi)
        assert origin == f"http://127.0.0.1:{auth.port}"
        assert origin in auth.effective_origins and lan_listener.origin() == origin
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.get(f"{origin}/ping")).text == "pong"
        # Reading again while nothing changed keeps the same listener.
        assert await lan_listener.apply(auth) == origin
        lan_listener.remember(False)
        assert await lan_listener.apply(auth) == ""
        assert origin not in auth.effective_origins and lan_listener.origin() == ""
        async with httpx.AsyncClient(trust_env=False) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(f"{origin}/ping")

    asyncio.run(scenario())


def test_a_taken_port_turns_nothing_on_and_the_worker_keeps_running(wifi):
    async def scenario():
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        try:
            auth = _Auth(holder.getsockname()[1])
            lan_listener.remember(True)
            assert await lan_listener.apply(auth, wifi) == ""
            assert not auth.effective_origins and lan_listener.origin() == ""
        finally:
            holder.close()
        return "still running"

    assert asyncio.run(scenario()) == "still running"
