"""Switching the Agent of an existing conversation from the composer."""
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.runtime.conversation.always_on import runtime, store
from openprogram.runtime.agent_registry import manager


def _client(tmp_path, monkeypatch, *, running=False):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.routes.execution import session_agent

    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Default Agent", make_default=True)
    manager.create("watcher", name="Docs Watcher", provider="anthropic", model_id="claude-opus-5-5")
    manager.update("watcher", {"always_on": {"enabled": True, "wake_minutes": 30}})

    class Db:
        sessions = {"c1": {"id": "c1", "agent_id": "main", "source": "", "archived": False}}

        def is_created(self, sid):
            return sid in self.sessions

        def get_session(self, sid):
            return self.sessions.get(sid)

        def update_session(self, sid, **fields):
            self.sessions[sid].update(fields)

        def update_branch_config(self, sid, branch_id, values, **_guards):
            self.sessions[sid].update(values)
            return dict(self.sessions[sid])

        def _session_dir(self, sid):
            folder = tmp_path / "sessions" / sid
            folder.mkdir(parents=True, exist_ok=True)
            return folder

        def state_file(self, sid, name):
            return self._session_dir(sid) / name

    db = Db()
    pins, frames = [], []
    live = {"c1": {"id": "c1", "agent_id": "main", "runtime": None}}
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr(runtime, "_db", lambda: db)
    monkeypatch.setattr(runtime, "_broadcast", lambda frame: frames.append(frame))
    monkeypatch.setattr("openprogram.runtime.conversation.session_model.write_session_chat_model",
                        lambda sid, provider, model: pins.append((sid, provider, model)))
    monkeypatch.setattr(_s, "_sessions", live, raising=False)
    monkeypatch.setattr(_s, "_sessions_lock", threading.Lock(), raising=False)
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: running, raising=False)
    # The switch holds only its own branch: a turn running there refuses it.
    monkeypatch.setattr(_s, "_is_selected_run_active", lambda sid: running, raising=False)
    monkeypatch.setattr(_s, "_broadcast", lambda msg: frames.append(msg), raising=False)
    monkeypatch.setattr(_s, "_get_provider_info", lambda sid: {}, raising=False)
    monkeypatch.setattr(session_agent, "emit_ws_frame", lambda frame: frames.append(frame))
    app = FastAPI()
    session_agent.register(app)
    return TestClient(app), db, live, pins, frames


def test_a_conversation_switches_to_an_always_on_agent(tmp_path, monkeypatch) -> None:
    client, db, live, pins, frames = _client(tmp_path, monkeypatch)
    response = client.post("/api/sessions/c1/agent", json={"agent_id": "watcher"})
    assert response.status_code == 200
    assert response.json()["model"] == {"provider": "anthropic", "id": "claude-opus-5-5"}
    assert db.sessions["c1"]["agent_id"] == "watcher"
    assert live["c1"]["agent_id"] == "watcher"
    # The conversation follows its new Agent's model; nothing is copied onto
    # it or onto the live mirror.
    assert live["c1"]["provider_name"] is None
    assert db.sessions["c1"]["provider_override"] is None
    assert db.sessions["c1"]["model_override"] is None
    # Picking the Agent counts as use: it joins with its first wake one interval out.
    state = store.load_state("c1")
    assert state["next_wake_at"] - state["joined_at"] == 30 * 60
    assert any('"agent_id": "watcher"' in frame for frame in frames if isinstance(frame, str))


def test_switching_is_refused_while_a_turn_runs(tmp_path, monkeypatch) -> None:
    client, db, *_ = _client(tmp_path, monkeypatch, running=True)
    assert client.post("/api/sessions/c1/agent", json={"agent_id": "watcher"}).status_code == 409
    assert db.sessions["c1"]["agent_id"] == "main"


def test_unknown_agent_or_conversation(tmp_path, monkeypatch) -> None:
    client, *_ = _client(tmp_path, monkeypatch)
    assert client.post("/api/sessions/c1/agent", json={"agent_id": "nope"}).status_code == 404
    assert client.post("/api/sessions/c9/agent", json={"agent_id": "main"}).status_code == 404
    assert client.post("/api/sessions/c1/agent", json={}).status_code == 400


def test_trial_agent_binding_cannot_switch(tmp_path, monkeypatch):
    client, db, live, *_ = _client(tmp_path, monkeypatch)
    db.sessions["c1"]["agent_trial"] = True
    response = client.post("/api/sessions/c1/agent", json={"agent_id": "watcher"})
    assert response.status_code == 409
    assert db.sessions["c1"]["agent_id"] == "main"
    assert live["c1"]["agent_id"] == "main"
