"""Open from anywhere: the embedded Tailscale sidecar, as the Worker runs and reports it.

The sidecar is replaced by a fake process that prints the same JSON state
lines ``openprogram-tailnet`` prints.
"""
from __future__ import annotations

import io
import json
import queue
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.host.security import remote_access as ways
from openprogram.interfaces.server.owner_auth import OwnerAuthState
from openprogram.interfaces.server.routes.identity import remote_access as routes

PUBLIC = "https://openprogram-mac.tail1234.ts.net"


class FakeProcess:
    """A sidecar that says what the test tells it to, and exits when told."""

    def __init__(self, command, **options):
        self.command = command
        self.options = options
        self.returncode = None
        self.stdin = io.StringIO()
        self._lines: queue.Queue = queue.Queue()
        self.stdout = iter(self._lines.get, None)

    def say(self, **state) -> None:
        self._lines.put(json.dumps(state) + "\n")

    def exit(self, code: int) -> None:
        self.returncode = code
        self._lines.put(None)

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.exit(-15)

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def profile(monkeypatch):
    """A profile of this test's own, so its config starts empty."""
    monkeypatch.setenv("OPENPROGRAM_PROFILE", f"anywhere-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def sidecar(monkeypatch, profile, tmp_path):
    """A fresh manager whose processes are fakes; ``spawned`` lists them."""
    binary = tmp_path / "openprogram-tailnet"
    binary.write_text("")
    monkeypatch.setenv("OPENPROGRAM_TAILNET_BIN", str(binary))
    spawned: list[FakeProcess] = []

    def popen(command, **options):
        process = FakeProcess(command, **options)
        spawned.append(process)
        return process

    monkeypatch.setattr(ways.subprocess, "Popen", popen)
    manager = ways.Anywhere()
    monkeypatch.setattr(ways, "anywhere", manager)
    monkeypatch.setattr(ways, "lan_origin", lambda port: "")
    return manager, spawned


def _auth() -> OwnerAuthState:
    return OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                         bind_host="127.0.0.1", port=18100, allowed_origins=())


def _until(check, seconds: float = 3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


# -- state lines --------------------------------------------------------------

def test_state_lines_are_read_and_anything_else_is_ignored():
    assert ways.parse_line('{"state":"needs_login","login_url":"https://login.tailscale.com/a/x"}') == {
        "state": "needs_login", "url": None, "login_url": "https://login.tailscale.com/a/x",
        "enable_url": None, "message": None}
    assert ways.parse_line('{"state":"running","url":"%s"}' % PUBLIC)["url"] == PUBLIC
    assert ways.parse_line('{"state":"needs_funnel","enable_url":"","message":"m"}') == {
        "state": "needs_funnel", "url": None, "login_url": None, "enable_url": None, "message": "m"}
    for line in ("tsnet: log noise", "[]", '{"state":"off"}', '{"state":"bogus"}', '{"url":"x"}', ""):
        assert ways.parse_line(line) is None
    assert ways.parse_line('{"state":"error","message":7}')["message"] is None


def test_the_tailnet_name_is_one_dns_label_and_names_the_profile(monkeypatch):
    monkeypatch.setattr(ways.socket, "gethostname", lambda: "Jane's MacBook_Pro.local")
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    assert ways.node_name() == "openprogram-dev-jane-s-macbook-pro"
    monkeypatch.delenv("OPENPROGRAM_PROFILE")
    monkeypatch.setattr(ways.socket, "gethostname", lambda: "x" * 100)
    name = ways.node_name()
    assert len(name) <= 63 and name.startswith("openprogram-")


# -- config ---------------------------------------------------------------------

def test_the_switch_is_kept_in_config(profile):
    from openprogram.state.config import read_config

    assert ways.anywhere_configured() is False
    ways.set_anywhere_configured(True)
    assert ways.anywhere_configured() is True
    assert read_config()["web"]["anywhere"] is True
    ways.set_anywhere_configured(False)
    assert ways.anywhere_configured() is False


def test_the_worker_brings_the_sidecar_back_only_when_left_on(sidecar):
    manager, spawned = sidecar
    auth = _auth()
    ways.start_anywhere_if_configured(18100, auth.allow_origins)
    assert spawned == []
    ways.set_anywhere_configured(True)
    ways.start_anywhere_if_configured(18100, auth.allow_origins)
    assert len(spawned) == 1
    manager.stop()


# -- the running sidecar ------------------------------------------------------

def test_the_address_is_answered_while_running_and_dropped_when_stopped(sidecar):
    manager, spawned = sidecar
    auth = _auth()
    ways.set_anywhere_configured(True)
    manager.start(18100, auth.allow_origins)
    process = spawned[0]
    command = process.command
    assert command[command.index("--target") + 1] == "http://127.0.0.1:18100"
    assert Path(command[command.index("--state-dir") + 1]) == ways.state_dir()
    assert ways.state_dir().is_dir()
    assert command[command.index("--hostname") + 1].startswith("openprogram-")
    assert manager.status()["state"] == "starting"

    process.say(state="needs_login", login_url="https://login.tailscale.com/a/abc")
    status = _until(lambda: manager.status() if manager.status()["state"] == "needs_login" else None)
    assert status == {"on": True, "state": "needs_login", "address": None,
                      "login_url": "https://login.tailscale.com/a/abc", "enable_url": None, "message": None}

    process.say(state="needs_funnel", enable_url="https://login.tailscale.com/f/funnel?node=n", message="m")
    status = _until(lambda: manager.status() if manager.status()["state"] == "needs_funnel" else None)
    assert status["enable_url"] == "https://login.tailscale.com/f/funnel?node=n" and status["login_url"] is None

    process.say(state="running", url=PUBLIC)
    _until(lambda: PUBLIC in auth.effective_origins)
    assert manager.status()["address"] == PUBLIC
    assert manager.address() == PUBLIC

    manager.start(18100, auth.allow_origins)  # already running: kept
    assert len(spawned) == 1

    ways.set_anywhere_configured(False)
    manager.stop()
    assert process.returncode is not None and process.stdin.closed
    assert PUBLIC not in auth.effective_origins
    assert manager.status() == {"on": False, "state": "off", "address": None, "login_url": None,
                                "enable_url": None, "message": None}


def test_a_sidecar_that_exits_reports_an_error_and_stops_answering(sidecar):
    manager, spawned = sidecar
    auth = _auth()
    ways.set_anywhere_configured(True)
    manager.start(18100, auth.allow_origins)
    spawned[0].say(state="running", url=PUBLIC)
    _until(lambda: PUBLIC in auth.effective_origins)
    spawned[0].exit(1)
    status = _until(lambda: manager.status() if manager.status()["state"] == "error" else None)
    assert "exit code 1" in status["message"] and status["address"] is None
    assert PUBLIC not in auth.effective_origins
    # Switching on again starts a new one.
    manager.start(18100, auth.allow_origins)
    assert len(spawned) == 2
    manager.stop()


def test_the_sidecar_goes_online_through_the_system_proxy(monkeypatch, sidecar):
    """An App-launched Worker has no proxy variables; behind a proxy Tailscale is reachable only through it."""
    manager, spawned = sidecar
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
                "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ways.urllib.request, "getproxies",
                        lambda: {"https": "http://127.0.0.1:7890", "http": "http://127.0.0.1:7890"})
    ways.set_anywhere_configured(True)
    manager.start(18100, _auth().allow_origins)
    env = spawned[0].options["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890" and env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    manager.stop()
    # A proxy the environment already names is kept as it is.
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    assert ways.child_env()["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert "HTTP_PROXY" not in ways.child_env()


def test_a_sidecar_that_never_gets_past_starting_says_it_cannot_reach_tailscale(monkeypatch, sidecar):
    manager, spawned = sidecar
    ways.set_anywhere_configured(True)
    manager.start(18100, _auth().allow_origins)
    assert manager.status()["state"] == "starting"
    monkeypatch.setattr(ways, "_REACH_SECONDS", 0.0)
    status = manager.status()
    assert status["state"] == "error" and "reach Tailscale" in status["message"]
    # It keeps trying; a sign-in link that comes late replaces the error.
    spawned[0].say(state="needs_login", login_url="https://login.tailscale.com/a/x")
    _until(lambda: manager.status()["state"] == "needs_login")
    manager.stop()


def test_a_missing_binary_is_reported(monkeypatch, sidecar):
    manager, spawned = sidecar
    monkeypatch.setattr(ways, "binary_path", lambda: None)
    ways.set_anywhere_configured(True)
    manager.start(18100, _auth().allow_origins)
    assert spawned == []
    status = manager.status()
    assert status["state"] == "error" and "openprogram-tailnet" in status["message"]


# -- HTTP contract --------------------------------------------------------------

def _client(auth: OwnerAuthState) -> TestClient:
    app = FastAPI()
    app.state.owner_auth = auth
    app.include_router(routes.router)
    return TestClient(app)


def test_the_http_contract(sidecar):
    manager, spawned = sidecar
    auth = _auth()
    client = _client(auth)

    body = client.get("/api/auth/remote-access").json()
    assert set(body) == {"lan", "anywhere"}
    assert set(body["lan"]) == {"on", "address"}
    assert body["anywhere"] == {"on": False, "state": "off", "address": None, "login_url": None,
                                "enable_url": None, "message": None}

    body = client.put("/api/auth/remote-access", json={"anywhere": True}).json()
    assert body["anywhere"]["on"] is True and body["anywhere"]["state"] == "starting"
    assert ways.anywhere_configured() is True
    spawned[0].say(state="running", url=PUBLIC)
    _until(lambda: client.get("/api/auth/remote-access").json()["anywhere"]["address"] == PUBLIC)

    # Pairing names the anywhere address once it answers.
    assert routes.routes_for_pairing(type("R", (), {"app": client.app})()) == [PUBLIC]

    body = client.put("/api/auth/remote-access", json={"anywhere": False}).json()
    assert body["anywhere"] == {"on": False, "state": "off", "address": None, "login_url": None,
                                "enable_url": None, "message": None}
    assert ways.anywhere_configured() is False
    assert PUBLIC not in auth.effective_origins
    assert routes.routes_for_pairing(type("R", (), {"app": client.app})()) == []


def test_pairing_names_this_network_before_anywhere(monkeypatch, sidecar):
    manager, spawned = sidecar
    lan = "http://192.168.1.20:18100"
    from openprogram.interfaces.server import lan_listener
    monkeypatch.setattr(lan_listener, "origin", lambda: lan)
    auth = _auth()
    manager.start(18100, auth.allow_origins)
    spawned[0].say(state="running", url=PUBLIC)
    _until(lambda: PUBLIC in auth.effective_origins)
    request = type("R", (), {"app": type("A", (), {"state": type("S", (), {"owner_auth": auth})()})()})()
    assert routes.routes_for_pairing(request) == [lan, PUBLIC]
    manager.stop()
