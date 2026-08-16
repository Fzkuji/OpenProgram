from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.agent.management import manager
from openprogram.webui.routes import agents


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Default Agent", make_default=True)
    app = FastAPI()
    agents.register(app)
    return TestClient(app)


def test_agent_tools_can_be_read_and_updated(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = client.get("/api/agents/main")
    assert detail.status_code == 200
    assert detail.json()["agent"]["id"] == "main"

    response = client.patch(
        "/api/agents/main",
        json={"tools": {"mode": "selected", "allowed": ["read", "research_agent"]}},
    )
    assert response.status_code == 200
    assert response.json()["agent"]["tools"] == {
        "mode": "selected",
        "allowed": ["read", "research_agent"],
    }
    assert manager.get("main").tools == response.json()["agent"]["tools"]

    response = client.patch(
        "/api/agents/main",
        json={"tools": {"mode": "automatic"}},
    )
    assert response.json()["agent"]["tools"] == {"mode": "automatic"}


def test_agent_tools_route_rejects_invalid_policy(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.patch(
        "/api/agents/main",
        json={"tools": {"mode": "selected", "allowed": "read"}},
    )
    assert response.status_code == 400
    assert manager.get("main").tools == {"mode": "automatic"}


def test_agent_tools_route_returns_404_for_unknown_agent(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/agents/missing").status_code == 404
    assert client.patch(
        "/api/agents/missing",
        json={"tools": {"mode": "none"}},
    ).status_code == 404
