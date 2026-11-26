"""POST /api/model and /api/agent_settings answer 4xx, not 500, to mistyped bodies.

Both routes checked only key presence: ``{"model": null}`` crashed on
``.strip()`` and a non-dict ``chat`` / ``exec`` section crashed on ``.get``
— each surfaced as an unhandled AttributeError (HTTP 500) instead of the
routes' own 400 contract. The guards mirror the sibling routes that already
type-check nested bodies.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.interfaces.server.routes.execution import runtime as execution_routes


@pytest.fixture
def client(monkeypatch):
    import openprogram.interfaces.server.server as server

    monkeypatch.setattr(server, "_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_init_providers", lambda: None)
    app = FastAPI()
    execution_routes.register(app)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("body", [
    {},
    {"model": None},
    {"model": 5},
    {"model": ["m"]},
])
def test_model_route_answers_400_to_a_mistyped_model(client, body):
    response = client.post("/api/model", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "Missing model"


@pytest.mark.parametrize("body", [
    {"chat": "gpt"},
    {"exec": []},
    {"session_id": "s1", "chat": "gpt"},
])
def test_agent_settings_ignores_mistyped_sections_without_crashing(client, body):
    response = client.post("/api/agent_settings", json=body)
    assert response.status_code == 200
    assert "chat" in response.json()
