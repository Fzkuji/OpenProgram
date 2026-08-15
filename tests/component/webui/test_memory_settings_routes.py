from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_settings_route_rejects_unavailable_embedding_method(monkeypatch):
    from openprogram.webui.routes.config import register

    saved = []
    monkeypatch.setattr(
        "openprogram.memory.retrieval.embedding.default_model_is_available",
        lambda: False,
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
    assert saved == []
