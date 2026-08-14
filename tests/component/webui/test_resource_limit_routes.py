from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_session_resource_limit_route_returns_effective_dto_and_requires_owner(tmp_path, monkeypatch):
    from openprogram.agent.session_db import SessionDB
    db = SessionDB(tmp_path / "sessions")
    db.create_session("s1", "main")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.agent.authority.owner_principal_id", lambda: "owner/id")
    app = FastAPI()
    from openprogram.webui.routes.config import register
    register(app)
    client = TestClient(app)
    assert client.put("/api/sessions/s1/resource-limits", json={}).status_code == 400
    assert client.put("/api/sessions/missing/resource-limits", json={"limits": {}, "base_revision": "x"}).status_code == 404
    denied = client.put("/api/sessions/s1/resource-limits", json={"limits": {"max_total_tokens": 9}, "base_revision": "x", "authority": {}})
    assert denied.status_code == 403
    claimed_owner = {"speaker_kind":"owner", "speaker_id":"owner/local", "speaker_display":"Owner", "authority_tier":"owner", "interaction":"interactive", "principal_id":"owner/id"}
    assert client.put("/api/sessions/s1/resource-limits", json={"limits": {"max_total_tokens": 9}, "base_revision": "x", "authority": claimed_owner}).status_code == 403
    assert client.get("/api/sessions/missing/resource-limits").status_code == 404
