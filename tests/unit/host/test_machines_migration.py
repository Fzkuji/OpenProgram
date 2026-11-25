"""The one-time rewrite of stored machines to one connection each (``operations/migrations``)."""
from __future__ import annotations

import json
import os

import pytest

from openprogram.host import machines
from openprogram.operations.migrations import machines_one_connection as migration

OLD = {
    "default_environment": "local",
    "local": {"window": {"kind": "desktop"}, "browser": {"kind": "built_in"}},
    "items": [
        {"id": "devbox", "name": "Dev box", "shell": {"kind": "ssh", "target": "me@devbox"},
         "window": {"kind": "desktop"}},
        {"id": "studio", "name": "Studio", "enabled": False,
         "shell": {"kind": "ssh", "target": "me@studio"},
         "openprogram": {"kind": "openprogram", "url": "http://192.168.1.20:18100", "token": {"secret": True}}},
        {"id": "lab", "name": "Lab VM", "screen": {"kind": "osworld", "url": "http://10.0.0.5:5000"}},
        {"id": "twin", "window": {"kind": "desktop"}, "browser": {"kind": "built_in"}},
        {"id": "box", "connection": {"kind": "docker"}},
    ],
}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"machines": OLD, "theme": "dark"}), encoding="utf-8")
    os.chmod(p, 0o600)
    state = tmp_path / "state"
    secrets = state / "auth" / "machines"
    secrets.mkdir(parents=True)
    (secrets / "studio.json").write_text(json.dumps({"openprogram.token": "t-1", "shell.password": "p"}))
    (secrets / "twin.json").write_text(json.dumps({"window.token": "t-2"}))
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    return p, state


def test_the_meaningful_slot_is_kept(profile):
    assert [migration.chosen_slot(item) for item in OLD["items"][:4]] == ["shell", "openprogram", "screen", ""]


def test_stored_machines_get_one_connection_and_their_secrets_follow(profile):
    path, state = profile
    assert migration.run() is True
    cfg = json.loads(path.read_text())
    assert cfg["theme"] == "dark"
    section = cfg["machines"]
    assert "local" not in section
    assert section["items"] == [
        {"id": "devbox", "name": "Dev box", "connection": {"kind": "ssh", "target": "me@devbox"}},
        {"id": "studio", "name": "Studio", "enabled": False, "connection": {
            "kind": "openprogram", "url": "http://192.168.1.20:18100", "token": {"secret": True}}},
        {"id": "lab", "name": "Lab VM", "connection": {"kind": "osworld", "url": "http://10.0.0.5:5000"}},
        {"id": "box", "connection": {"kind": "docker"}},
    ]
    secrets = state / "auth" / "machines"
    assert json.loads((secrets / "studio.json").read_text()) == {"token": "t-1"}
    # A machine that reached only this computer is gone, with its secrets.
    assert not (secrets / "twin.json").exists()
    assert (state / "migrations" / "machines-one-connection.done").exists()
    # The registry reads the result as is.
    studio = machines.get_machine("studio")
    assert studio.method == "openprogram" and machines.secret(studio, "token") == "t-1"
    assert [machine.id for machine in machines.list_machines()] == ["local", "devbox", "studio", "lab", "box"]


def test_it_runs_once(profile):
    path, _ = profile
    assert migration.run() is True
    path.write_text(json.dumps({"machines": OLD}), encoding="utf-8")
    assert migration.run() is False
    assert json.loads(path.read_text())["machines"] == OLD


def test_a_profile_already_in_the_new_shape_is_left_alone(profile):
    path, state = profile
    path.write_text(json.dumps({"machines": {"items": [{"id": "box", "connection": {"kind": "docker"}}]}}))
    (state / "auth" / "machines" / "studio.json").unlink()
    (state / "auth" / "machines" / "twin.json").unlink()
    assert migration.run() is False
    assert json.loads(path.read_text())["machines"]["items"] == [{"id": "box", "connection": {"kind": "docker"}}]


def test_the_worker_runs_it_at_start():
    from pathlib import Path
    runner = Path(migration.__file__).parents[1] / "worker" / "runner.py"
    assert "machines_one_connection" in runner.read_text(encoding="utf-8")
