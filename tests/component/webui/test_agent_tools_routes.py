import multiprocessing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.agent.management import manager
from openprogram.webui.routes import agents


def _client(tmp_path, monkeypatch, *, raise_server_exceptions: bool = True) -> TestClient:
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Default Agent", make_default=True)
    app = FastAPI()
    agents.register(app)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _create_agent_process(root: str, results) -> None:
    manager._state_root = lambda: Path(root)
    try:
        results.put(("ok", manager.create_from_name("Concurrent Agent").id))
    except Exception as exc:  # pragma: no cover - assertion reports the child error
        results.put(("error", repr(exc)))


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


def test_agent_creation_generates_unique_ids_from_the_name(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    first = client.post("/api/agents", json={"name": "Research Agent"})
    second = client.post("/api/agents", json={"name": "Research Agent"})
    non_latin = client.post("/api/agents", json={"name": "研究助手"})

    assert first.status_code == 201
    assert first.json()["agent"]["id"] == "research-agent"
    assert second.status_code == 201
    assert second.json()["agent"]["id"] == "research-agent-2"
    assert non_latin.status_code == 201
    assert non_latin.json()["agent"]["id"] == "agent"


def test_agent_creation_requires_only_a_non_empty_name(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    missing = client.post("/api/agents", json={})

    assert missing.status_code == 400
    assert missing.json()["error"] == "name must be a string"


def test_explicit_agent_ids_remain_backward_compatible(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    created = client.post("/api/agents", json={"id": "legacy-agent"})
    duplicated = client.post(
        "/api/agents/main/duplicate", json={"id": "legacy-copy"},
    )

    assert created.status_code == 201
    assert created.json()["agent"]["name"] == "Legacy Agent"
    assert duplicated.status_code == 201
    assert duplicated.json()["agent"]["name"] == "Legacy Copy"


def test_name_only_creation_is_unique_across_processes(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [
        context.Process(target=_create_agent_process, args=(str(tmp_path), results))
        for _ in range(8)
    ]
    for process in processes:
        process.start()
    rows = [results.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)

    assert all(process.exitcode == 0 for process in processes)
    assert all(status == "ok" for status, _ in rows), rows
    assert {value for _, value in rows} == {
        "concurrent-agent",
        "concurrent-agent-2",
        "concurrent-agent-3",
        "concurrent-agent-4",
        "concurrent-agent-5",
        "concurrent-agent-6",
        "concurrent-agent-7",
        "concurrent-agent-8",
    }


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
    assert Path(payload["path"]).parts[-3:] == ("agents", "main_copy", "workspace")
    assert [row["name"] for row in payload["files"]] == [
        "AGENTS.md",
        "SOUL.md",
        "USER.md",
        "TOOLS.md",
    ]

    generated = client.post(
        "/api/agents/main/duplicate",
        json={"name": "Main Copy"},
    )
    generated_again = client.post(
        "/api/agents/main/duplicate",
        json={"name": "Main Copy"},
    )
    assert generated.status_code == 201
    assert generated.json()["agent"]["id"] == "main-copy"
    assert generated_again.status_code == 201
    assert generated_again.json()["agent"]["id"] == "main-copy-2"
    assert generated.json()["agent"]["system_prompt"] == "Copied instructions."


def test_agent_duplicate_rolls_back_when_registry_write_fails(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, raise_server_exceptions=False)
    original_write_index = manager._write_index

    def fail_write_index(data) -> None:
        raise OSError("simulated registry failure")

    monkeypatch.setattr(manager, "_write_index", fail_write_index)
    response = client.post(
        "/api/agents/main/duplicate", json={"name": "Broken Copy"},
    )
    monkeypatch.setattr(manager, "_write_index", original_write_index)

    assert response.status_code == 500
    assert manager.get("broken-copy") is None
    assert not (tmp_path / "agents" / "broken-copy").exists()
    assert [agent.id for agent in manager.list_all()] == ["main"]


def test_agent_duplicate_preserves_preexisting_incomplete_directory(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    existing = tmp_path / "agents" / "reserved-copy"
    existing.mkdir(parents=True)
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    explicit = client.post(
        "/api/agents/main/duplicate", json={"id": "reserved-copy"},
    )
    generated = client.post(
        "/api/agents/main/duplicate", json={"name": "Reserved Copy"},
    )

    assert explicit.status_code == 400
    assert marker.read_text(encoding="utf-8") == "keep"
    assert generated.status_code == 201
    assert generated.json()["agent"]["id"] == "reserved-copy-2"
