"""Actual authenticated ASGI reads over real self-update records."""
from dataclasses import replace
import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openprogram.agent import authority
from openprogram.self_update import SelfUpdateStore, UpdatePhase
from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
from openprogram.webui.routes import running, self_updates
from tests.unit.self_update.test_store import _request


@pytest.fixture
def api(tmp_path, monkeypatch):
    from openprogram import paths
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    authority._reset_owner_cache_for_tests()
    auth = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id=authority.owner_principal_id(),
                                        bind_host="127.0.0.1", port=18100, allowed_origins=())
    app = FastAPI()
    app.add_middleware(OwnerAuthMiddleware, auth_state=auth)
    self_updates.register(app)
    running.register(app)
    store = SelfUpdateStore()
    client = TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 12345))
    yield client, {"Authorization": f"Bearer {auth.token}"}, store
    client.close()
    authority._reset_owner_cache_for_tests()


def test_owner_auth_and_session_scope_are_enforced(api):
    client, headers, store = api
    store.create(_request())
    url = "/api/self-updates/su_test?session_id=session-1"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/self-updates/su_test?session_id=other", headers=headers).status_code == 403
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["candidate_revision"] == "2" * 40
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/self-updates/su_test", headers=headers).status_code == 422


def test_empty_reads_and_historical_reads_do_not_change_store(api):
    client, headers, store = api
    response = client.get("/api/self-updates?session_id=session-1", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}
    assert not store.root.exists()
    store.create(_request())
    store.transition("su_test", UpdatePhase.ABORTED)
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    response = client.get("/api/self-updates?session_id=session-1&limit=1", headers=headers)
    assert response.json()["items"][0]["phase"] == "aborted"
    assert {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before
    assert not (store.root / "active.json").exists()


def test_running_reuses_projection_and_reports_read_failure(api, monkeypatch):
    client, headers, store = api
    monkeypatch.setattr(running, "_collect", lambda: [])
    store.create(_request())
    direct = client.get("/api/self-updates/su_test?session_id=session-1", headers=headers).json()
    snapshot = client.get("/api/running", headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["items"][0]["update"] == direct
    assert snapshot.json()["self_update_error"] is None
    path = store.root / "su_test" / "state.json"
    path.write_text('{"private_token":"DO_NOT_EXPOSE"}')
    snapshot = client.get("/api/running", headers=headers)
    assert snapshot.json()["self_update_error"] is not None
    assert "DO_NOT_EXPOSE" not in snapshot.text
    error = client.get("/api/self-updates/su_test?session_id=session-1", headers=headers)
    assert error.status_code == 409
    assert "DO_NOT_EXPOSE" not in error.text and str(store.root) not in error.text


def test_pagination_and_limits_through_public_route(api):
    client, headers, store = api
    for n in range(3):
        req = replace(_request(), update_id=f"su_{n}")
        store.create(req)
        store.transition(req.update_id, UpdatePhase.ABORTED)
    first = client.get("/api/self-updates?session_id=session-1&limit=2", headers=headers).json()
    second = client.get("/api/self-updates", headers=headers,
                        params={"session_id": "session-1", "limit": 2, "cursor": first["next_cursor"]}).json()
    assert [item["update_id"] for item in first["items"] + second["items"]] == ["su_2", "su_1", "su_0"]
    assert second["next_cursor"] is None
    assert client.get("/api/self-updates?session_id=session-1&limit=999", headers=headers).status_code == 422
    assert client.get("/api/self-updates/su_missing?session_id=session-1", headers=headers).status_code == 404
    assert "token" not in json.dumps(first)
