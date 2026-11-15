"""GET /api/history lists the live sessions newest first; a session record
carries no creation time of its own, so the route never fails over it."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.interfaces.server import server
from openprogram.interfaces.server.routes.files import workdir


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_sessions", {
        "s-old": {"id": "s-old", "title": "Old", "messages": [{"role": "user", "timestamp": 100.0}]},
        "s-new": {"id": "s-new", "title": "New", "messages": [{"role": "user", "created_at": 300.0}, {"role": "assistant"}]},
        "s-bare": {"id": "s-bare", "messages": []},
        "s-stamped": {"id": "s-stamped", "title": "Stamped", "created_at": 200.0, "messages": []},
    })
    app = FastAPI()
    workdir.register(app)
    return TestClient(app)


def test_history_lists_sessions_newest_first_without_a_stored_creation_time(client):
    response = client.get("/api/history")
    assert response.status_code == 200
    rows = response.json()
    assert [row["id"] for row in rows] == ["s-new", "s-stamped", "s-old", "s-bare"]
    assert [row["created_at"] for row in rows] == [300.0, 200.0, 100.0, 0.0]
    assert [row["message_count"] for row in rows] == [2, 0, 1, 0]
    assert rows[3]["title"] is None
