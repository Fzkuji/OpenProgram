"""The one-time rewrite of a profile's stored permission data."""
from __future__ import annotations

import asyncio
import json

from types import SimpleNamespace

from openprogram.operations.migrations import open_permissions


def _agent(state, agent_id, **fields):
    folder = state / "agents" / agent_id
    folder.mkdir(parents=True)
    (folder / "agent.json").write_text(json.dumps({"id": agent_id, "revision": 7, **fields}), encoding="utf-8")
    return folder / "agent.json"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _finish(monkeypatch):
    """The server's half of the run, with no approvals open."""
    service = SimpleNamespace(executions=SimpleNamespace())
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.runtime.execution.waits.DurableWaitStore",
                        lambda _store: SimpleNamespace(list_open=lambda: []))
    asyncio.run(open_permissions.resume_open_approvals())


def test_stored_holds_are_released_once_and_a_later_choice_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(open_permissions, "get_state_dir", lambda: tmp_path)
    held = _agent(tmp_path, "held", hold_outbound=True)
    free = _agent(tmp_path, "free", hold_outbound=False)
    unset = _agent(tmp_path, "unset")
    broken = _agent(tmp_path, "broken")
    broken.write_text("{not json", encoding="utf-8")

    assert open_permissions.run() is True
    assert _read(held) == {"id": "held", "revision": 7, "hold_outbound": False}
    assert _read(free)["hold_outbound"] is False
    assert "hold_outbound" not in _read(unset)
    assert broken.read_text(encoding="utf-8") == "{not json"
    _finish(monkeypatch)
    assert (tmp_path / "migrations" / "open-permissions.done").exists()

    held.write_text(json.dumps({"id": "held", "revision": 8, "hold_outbound": True}), encoding="utf-8")
    assert open_permissions.run() is False
    assert _read(held)["hold_outbound"] is True


def test_a_profile_without_agents_is_marked_done(tmp_path, monkeypatch):
    monkeypatch.setattr(open_permissions, "get_state_dir", lambda: tmp_path)
    assert open_permissions.run() is True
    _finish(monkeypatch)
    assert open_permissions.run() is False


def test_the_real_store_clears_only_a_field_the_owner_never_set(tmp_path):
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    store.create_session("old", "main")
    store.update_session("old", permission_mode="auto")

    assert store.clear_session_field("old", "permission_mode", when=lambda meta: False) is False
    assert store.get_session("old")["permission_mode"] == "auto"
    assert store.clear_session_field("old", "permission_mode", when=lambda meta: True) is True
    assert "permission_mode" not in store.read_disk_meta("old")
    assert store.get_session("old").get("permission_mode") is None
    assert store.clear_session_field("old", "permission_mode", when=lambda meta: True) is False
    assert store.clear_session_field("missing", "permission_mode", when=lambda meta: True) is False
