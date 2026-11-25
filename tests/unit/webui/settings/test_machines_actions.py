"""The machines wire between the worker and Settings or the composer.

Each action replies to its sender and, after a change, broadcasts the
whole ``machines`` snapshot; a secret setting never reaches a client.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from openprogram.host import machines
from openprogram.host.machines import kinds
from openprogram.interfaces.server.ws_actions import machines as actions


class _WS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.fixture
def wire(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(machines, "_remove_instances_later", lambda machine_id: None)
    # A listing checks a machine behind its answer; no test reaches out to a real one.
    from openprogram.host.machines import status
    status.forget()
    monkeypatch.setattr(status, "_check", lambda machine: None)
    broadcasts: list[dict] = []
    from openprogram.interfaces.server import server
    monkeypatch.setattr(server, "_broadcast", lambda text: broadcasts.append(json.loads(text)))
    kinds.register(kinds.Kind(
        name="test_vnc", label="Test VNC",
        settings={"type": "object", "properties": {"address": {"type": "string"},
                                                   "password": {"type": "string", "secret": True}}}))
    yield broadcasts
    kinds._kinds.pop("test_vnc", None)


def _call(action, **cmd):
    ws = _WS()
    asyncio.run(actions.ACTIONS[action](ws, {"action": action, "request_id": "r-1", **cmd}))
    return ws.sent


def _echo(action):
    """What every answer to a request carries, so a client settles that request and no other."""
    return {"action": action, "request_id": "r-1"}


def test_the_snapshot_lists_machines_and_every_method(wire):
    [reply] = _call("list_machines")
    assert reply["type"] == "machines"
    data = reply["data"]
    assert "default_environment" not in data
    assert [item["id"] for item in data["items"]] == ["local"]
    assert data["items"][0]["status"] == {"state": "connected", "message": ""}
    assert {"local", "ssh", "docker", "openprogram", "test_vnc"} <= {kind["name"] for kind in data["kinds"]}
    # What runs commands is the shell feature's to say, not the method's.
    assert data["items"][0]["connection"]["kind"] == "local"
    assert data["items"][0]["shell"] == {"remote_files": False, "instance_scope": "machine"}


def test_save_replies_and_broadcasts_without_the_secret(wire):
    [reply] = _call("save_machine", machine={
        "name": "Office PC", "connection": {"kind": "test_vnc", "address": "10.0.0.5:5900", "password": "hunter2"}})
    assert reply["type"] == "machine_result" and reply["data"]["ok"] is True
    assert reply["data"]["id"] == "office-pc"
    assert reply["data"]["machine"]["connection"]["password"] == machines.SECRET_MARKER
    [snapshot] = wire
    assert snapshot["type"] == "machines"
    assert "hunter2" not in json.dumps(snapshot) and "hunter2" not in json.dumps(reply)

    # Sending the machine back as it was read keeps the secret.
    _call("save_machine", machine={**reply["data"]["machine"], "name": "Office"})
    assert machines.secret(machines.get_machine("office-pc"), "password") == "hunter2"


def test_a_refused_save_says_why_and_broadcasts_nothing(wire):
    [reply] = _call("save_machine", machine={"id": "x", "connection": {"kind": "telnet"}})
    assert reply["data"] == {"ok": False, "error": reply["data"]["error"], "id": "x", **_echo("save_machine")}
    assert "unknown connection method" in reply["data"]["error"]
    assert wire == []


def test_switch_off_and_delete(wire):
    _call("save_machine", machine={"id": "box", "connection": {"kind": "docker"}})
    [reply] = _call("set_machine_enabled", id="box", enabled=False)
    assert reply["data"]["ok"] and reply["data"]["machine"]["enabled"] is False
    [box] = [item for item in wire[-1]["data"]["items"] if item["id"] == "box"]
    assert box["status"]["state"] == "disconnected"
    assert "set_default_environment" not in actions.ACTIONS

    [reply] = _call("delete_machine", id="box")
    assert reply["data"] == {"ok": True, "id": "box", **_echo("delete_machine")}
    assert [item["id"] for item in wire[-1]["data"]["items"]] == ["local"]

    [reply] = _call("delete_machine", id="local")
    assert reply["data"]["ok"] is False


def test_test_machine_answers_with_a_message(wire):
    [reply] = _call("test_machine", id="ghost")
    assert reply == {"type": "machine_test", "data": {"id": "ghost", "ok": False,
                                                      "message": "Unknown machine 'ghost'",
                                                      **_echo("test_machine")}}
    _call("save_machine", machine={"id": "lab", "connection": {"kind": "test_vnc", "address": "a:1"}})
    [reply] = _call("test_machine", id="lab")
    assert reply["data"] == {"id": "lab", "ok": True, "message": "Nothing to check", **_echo("test_machine")}


def test_every_action_is_dispatched_and_classified():
    from openprogram.interfaces.server import ws_errors
    from openprogram.interfaces.server.server_runtime import websocket_dispatch
    table = websocket_dispatch._build_ws_action_registry()
    for name, handler in actions.ACTIONS.items():
        assert table[name] is handler
        assert ws_errors._ACTION_SCOPES[name] == "settings"
    assert not {"list_environments", "save_environment", "test_environment"} & set(table)


def test_a_machines_state_follows_what_was_seen_and_is_broadcast(wire, monkeypatch):
    from openprogram.host.machines import status
    checked = []
    monkeypatch.setattr(status, "_check", lambda machine: checked.append(machine.id))
    _call("save_machine", machine={"id": "devbox", "connection": {"kind": "ssh", "target": "me@devbox"}})
    wire.clear()
    [reply] = _call("list_machines")
    [devbox] = [item for item in reply["data"]["items"] if item["id"] == "devbox"]
    # Never checked: it reads as connecting while a check starts behind the answer.
    assert devbox["status"]["state"] == "connecting"
    deadline = time.monotonic() + 5
    while "devbox" not in checked and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "devbox" in checked

    machine = machines.get_machine("devbox")
    status.note(machine, False, "ssh: connect to host devbox port 22: Connection refused")
    assert status.state_of(machine, refresh=False)["state"] == "error"
    assert wire and wire[-1]["type"] == "machines"
    status.note(machine, True)
    assert status.online(machine)
    status.forget()
