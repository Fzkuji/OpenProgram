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


def test_agent_lifecycle_and_complete_configuration(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    created = client.post(
        "/api/agents",
        json={"id": "research", "name": "Research Agent"},
    )
    assert created.status_code == 201
    assert created.json()["agent"]["id"] == "research"
    workspace = tmp_path / "agents" / "research" / "workspace"
    assert (workspace / "AGENTS.md").is_file()
    assert (workspace / "SOUL.md").is_file()
    assert (workspace / "USER.md").is_file()

    version = created.json()["agent"]["updated_at"]
    updated = client.patch(
        "/api/agents/research",
        json={
            "updated_at": version,
            "name": "Research",
            "model": {"provider": "openai", "id": "gpt-5.4"},
            "thinking_effort": "high",
            "system_prompt": "Research carefully.",
            "identity": {
                "name": "Research",
                "mention_patterns": ["@research", "@papers"],
            },
            "skills": {
                "allowed": ["research-*"],
                "disabled": ["research-live"],
                "categories": ["research"],
            },
            "tools": {"mode": "selected", "allowed": ["read", "web_search"]},
            "mcp": {
                "allowed": ["filesystem", "browser"],
                "disabled": ["linear"],
                "required": ["filesystem"],
            },
            "session_scope": "per-peer",
            "session_idle_minutes": 120,
            "session_daily_reset": "04:00",
        },
    )
    assert updated.status_code == 200
    agent = updated.json()["agent"]
    assert agent["model"] == {"provider": "openai", "id": "gpt-5.4"}
    assert agent["skills"]["categories"] == ["research"]
    assert agent["mcp"]["required"] == ["filesystem"]
    assert agent["session_scope"] == "per-peer"

    made_default = client.post("/api/agents/research/default")
    assert made_default.status_code == 200
    assert made_default.json()["agent"]["default"] is True
    assert manager.get("main").default is False

    blocked = client.delete("/api/agents/research")
    assert blocked.status_code == 409

    deleted = client.delete("/api/agents/main")
    assert deleted.status_code == 204
    assert manager.get("main") is None


def test_agent_update_rejects_conflicts_and_invalid_values(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    current = client.get("/api/agents/main").json()["agent"]

    stale = client.patch(
        "/api/agents/main",
        json={"updated_at": current["updated_at"] - 1, "name": "Stale"},
    )
    assert stale.status_code == 409
    assert manager.get("main").name == "Default Agent"

    invalid = client.patch(
        "/api/agents/main",
        json={
            "session_scope": "global",
            "session_idle_minutes": -1,
            "session_daily_reset": "25:00",
        },
    )
    assert invalid.status_code == 400

    contradictory = client.patch(
        "/api/agents/main",
        json={
            "mcp": {
                "allowed": ["filesystem"],
                "disabled": ["filesystem"],
                "required": ["filesystem"],
            },
        },
    )
    assert contradictory.status_code == 400


def test_agent_duplicate_copies_configuration_without_sessions(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.patch(
        "/api/agents/main",
        json={
            "model": {"provider": "openai", "id": "gpt-5.4"},
            "tools": {"mode": "none"},
            "system_prompt": "Copied instructions.",
        },
    )
    source_session = tmp_path / "agents" / "main" / "sessions" / "history.json"
    source_session.write_text("{}", encoding="utf-8")

    response = client.post(
        "/api/agents/main/duplicate",
        json={"id": "main_copy", "name": "Main Copy"},
    )
    assert response.status_code == 201
    duplicate = response.json()["agent"]
    assert duplicate["model"] == {"provider": "openai", "id": "gpt-5.4"}
    assert duplicate["tools"] == {"mode": "none"}
    assert duplicate["system_prompt"] == "Copied instructions."
    assert not (
        tmp_path / "agents" / "main_copy" / "sessions" / "history.json"
    ).exists()

    workspace = client.get("/api/agents/main_copy/workspace")
    assert workspace.status_code == 200
    payload = workspace.json()
    assert payload["path"].endswith("/agents/main_copy/workspace")
    assert [row["name"] for row in payload["files"]] == [
        "AGENTS.md",
        "SOUL.md",
        "USER.md",
        "TOOLS.md",
    ]
