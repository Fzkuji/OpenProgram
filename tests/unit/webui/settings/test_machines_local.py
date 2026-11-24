"""This computer on the machines wire, as Settings sends it.

Settings edits this computer with ``save_machine`` and the id ``local``:
only its window, screen and browser connections change, a null one puts the
built-in connection back, and a name or shell sent with it is ignored.
Only a kind that is this computer's own may be one of them; any other is
a machine of its own, so this computer never gets a second name. The
snapshot's ``kinds`` hold this computer's own kinds too, so its pickers have
choices.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from openprogram.host import machines
from openprogram.host.machines import kinds
from openprogram.interfaces.server.ws_actions import machines as actions
from openprogram.programs.tools.computer_use.computer_use import registry


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
    broadcasts: list[dict] = []
    from openprogram.interfaces.server import server
    monkeypatch.setattr(server, "_broadcast", lambda text: broadcasts.append(json.loads(text)))
    registry.load()
    kinds.get("screen", "")
    monkeypatch.setitem(kinds._kinds, ("window", "test_other"), kinds.Kind(
        name="test_other", connection="window", label="Other windows", local_only=True,
        settings={"type": "object", "properties": {"address": {"type": "string"}}}))
    return p


def _call(action, **cmd):
    ws = _WS()
    asyncio.run(actions.ACTIONS[action](ws, cmd))
    return ws.sent


def _stored(p):
    return json.loads(p.read_text(encoding="utf-8")).get("machines") or {}


def test_the_snapshot_holds_this_computers_own_kinds(wire):
    [reply] = _call("list_machines")
    found = {(kind["connection"], kind["name"]): kind for kind in reply["data"]["kinds"]}
    assert found[("window", "desktop")]["local_only"] is True
    assert found[("screen", "desktop")]["local_only"] is True
    assert found[("browser", "built_in")]["local_only"] is True
    assert found[("shell", "local")]["local_only"] is True
    assert ("screen", "osworld") in found


def test_saving_this_computer_changes_only_its_window_screen_and_browser(wire):
    [reply] = _call("save_machine", machine={"id": "local", "name": "My Mac", "shell": {"kind": "ssh", "target": "x"},
                                             "window": {"kind": "test_other", "address": "10.0.0.3:1"},
                                             "screen": None, "browser": {"kind": "built_in"}})
    assert reply["type"] == "machine_result" and reply["data"]["ok"] is True and reply["data"]["id"] == "local"
    assert _stored(wire)["local"] == {"window": {"kind": "test_other", "address": "10.0.0.3:1"}}
    local = machines.local_machine()
    assert (local.name, local.shell.kind, local.browser.kind) == ("This computer", "local", "built_in")
    assert reply["data"]["machine"]["window"]["kind"] == "test_other"
    # Without the whole screen on, this computer has no screen connection to show.
    assert "screen" not in reply["data"]["machine"]
    assert "items" not in _stored(wire) or _stored(wire)["items"] == []


def test_a_null_connection_puts_the_built_in_one_back(wire):
    _call("save_machine", machine={"id": "local", "window": {"kind": "test_other", "address": "a"}})
    [reply] = _call("save_machine", machine={"id": "local", "window": None, "screen": None, "browser": None})
    assert reply["data"]["ok"] is True
    assert "local" not in _stored(wire)
    assert machines.local_machine().window.kind == "desktop"


def test_a_refused_connection_of_this_computer_says_why(wire):
    [reply] = _call("save_machine", machine={"id": "local", "screen": {"kind": "no_such_kind"}})
    assert reply["data"]["ok"] is False and "unknown screen kind" in reply["data"]["error"]
    assert reply["data"]["id"] == "local"


def test_a_kind_of_another_machine_is_refused_for_this_computer(wire):
    [reply] = _call("save_machine", machine={"id": "local", "screen": {"kind": "osworld",
                                                                       "url": "http://127.0.0.1:5000"}})
    assert reply["data"]["ok"] is False
    assert reply["data"]["error"] == ("Refused: this computer's screen can only be its own; add the OSWorld "
                                      "server as a machine of its own.")
    assert "local" not in _stored(wire)


def test_a_hand_written_kind_of_another_machine_is_ignored(wire):
    wire.write_text(json.dumps({"computer_use": {"whole_screen": True},
                                "machines": {"local": {"screen": {"kind": "osworld",
                                                                  "url": "http://127.0.0.1:5000"}}}}),
                    encoding="utf-8")
    assert machines.local_machine().screen.kind == "desktop"
