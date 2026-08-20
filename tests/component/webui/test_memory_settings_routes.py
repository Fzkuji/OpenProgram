from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_memory_settings_scope_skips_dynamic_provider_and_tool_rows(monkeypatch):
    from openprogram.webui.routes.config import register

    monkeypatch.setattr("openprogram.setup._read_config", lambda: {})
    monkeypatch.setattr(
        "openprogram.providers.registry.check_providers",
        lambda: (_ for _ in ()).throw(
            AssertionError("memory settings must not inspect providers")
        ),
    )
    monkeypatch.setattr(
        "openprogram.programs.list_registered_agent_tools",
        lambda: (_ for _ in ()).throw(
            AssertionError("memory settings must not enumerate tools")
        ),
    )
    app = FastAPI()
    register(app)

    response = TestClient(app).get("/api/settings?scope=memory")

    assert response.status_code == 200
    assert response.json()["settings"]
    assert all(
        row["key"].startswith("memory.")
        for row in response.json()["settings"]
    )


def test_settings_route_rejects_unavailable_embedding_method(monkeypatch):
    import asyncio

    from openprogram.webui.routes.config import register

    saved = []
    inside_worker = False

    async def fake_to_thread(func, *args, **kwargs):
        nonlocal inside_worker
        inside_worker = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_worker = False

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        "openprogram.memory.retrieval.embedding.default_model_is_available",
        lambda: saved.append(("validate", inside_worker)) or False,
    )
    monkeypatch.setattr(
        "openprogram.setup.update_config", lambda mutator: saved.append(mutator),
    )
    app = FastAPI()
    register(app)

    response = TestClient(app).post(
        "/api/settings",
        json={"key": "memory.retrieval.method", "value": "hybrid"},
    )

    assert response.status_code == 400
    assert "not available" in response.json()["error"]
    assert saved == [("validate", True)]


def test_settings_route_accepts_agent_recall_without_embedding(monkeypatch):
    from openprogram.webui.routes.config import register

    saved = []
    monkeypatch.setattr(
        "openprogram.memory.retrieval.embedding.default_model_is_available",
        lambda: (_ for _ in ()).throw(
            AssertionError("Agent recall must not probe embedding")
        ),
    )
    monkeypatch.setattr(
        "openprogram.setup.update_config", lambda mutator: saved.append(mutator),
    )
    app = FastAPI()
    register(app)

    response = TestClient(app).post(
        "/api/settings",
        json={"key": "memory.retrieval.method", "value": "agent"},
    )

    assert response.status_code == 200
    assert response.json()["value"] == "agent"
    assert len(saved) == 1
