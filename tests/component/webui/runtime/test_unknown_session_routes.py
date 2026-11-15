"""A conversation that does not exist is not found on its GET routes.

``load_session`` answers ``session_not_found`` for an id no conversation
has and for a deleted one. The per-conversation reads answer the same way
instead of estimating a conversation that is not there. A conversation the
server holds in memory before its first write (a draft's first Send) is
known.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROUTES = ("context", "tokens", "branches/tokens", "context-range", "dag")


@pytest.fixture
def client(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    from openprogram.runtime import lower_layers
    from openprogram.state.store.session import default_store, session_store

    monkeypatch.setattr(session_store.shared, "_default_store", None)
    lower_layers.register()
    store = default_store()
    store.create_session("kept", "main")
    store.append_message("kept", {"id": "u1", "role": "user", "content": "hi"})
    store.create_session("gone", "main")
    store.append_message("gone", {"id": "u1", "role": "user", "content": "hi"})
    store.delete_session("gone")

    app = FastAPI()
    from openprogram.interfaces.server.routes.files import tree

    tree.register(app)
    return TestClient(app)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("session_id", ["gone", "never_existed"])
def test_missing_conversation_is_not_found(client, route, session_id):
    response = client.get(f"/api/sessions/{session_id}/{route}")

    assert response.status_code == 404
    assert response.json() == {"error": "session_not_found"}


@pytest.mark.parametrize("route", ROUTES)
def test_existing_conversation_still_answers(client, route):
    response = client.get(f"/api/sessions/kept/{route}")

    assert response.status_code == 200
    assert response.json().get("error") is None


def test_conversation_held_in_memory_before_its_first_write_is_known(client):
    """A draft's first Send: the server holds the conversation before the
    store has written it, and the composer asks for its context then."""
    from openprogram.interfaces.server import server

    server._sessions["just_sent"] = {}
    try:
        response = client.get("/api/sessions/just_sent/context")
    finally:
        server._sessions.pop("just_sent", None)

    assert response.status_code == 200
    assert response.json().get("error") is None


def test_a_probe_of_a_missing_conversation_leaves_no_lock_file(client, tmp_path):
    client.get("/api/sessions/never_existed/context")
    client.get("/api/sessions/gone/context")

    locks = tmp_path / "state" / "projects" / ".locks"
    names = {path.name for path in locks.iterdir()} if locks.is_dir() else set()
    assert "never_existed.lock" not in names
    assert "gone.lock" not in names
