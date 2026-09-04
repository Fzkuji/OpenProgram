"""Actual authenticated ASGI reads over real self-update records."""
from dataclasses import replace
import json
import time

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


@pytest.mark.parametrize("pointer_name", ["diagnosis-pending.json", "source-repair-pending.json", "iteration-pending.json"])
@pytest.mark.parametrize("missing", [False, True])
def test_running_pending_target_must_exist_without_repair(api, monkeypatch, pointer_name, missing):
    client, headers, store = api
    monkeypatch.setattr(running, "_collect", lambda: [])
    store.create(_request())
    store.transition("su_test", UpdatePhase.ABORTED)
    pointer = store.root / pointer_name
    store._write_json(pointer, {"schema": 1, "update_id": "su_missing" if missing else "su_test"})
    before = pointer.read_bytes(), pointer.stat().st_mtime_ns

    response = client.get("/api/running", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    if missing:
        assert payload["self_update_error"] is not None
        assert payload["items"] == []
    else:
        assert payload["self_update_error"] is None
        assert payload["items"][0]["update"]["update_id"] == "su_test"
    assert (pointer.read_bytes(), pointer.stat().st_mtime_ns) == before


@pytest.mark.parametrize("mode", [0o600, 0o400, 0o640, 0o604, 0o644, 0o660])
def test_rollback_projection_requires_private_proof_without_chmod(api, mode):
    from openprogram.self_update.recovery import SYSTEM_CHECKS
    from openprogram.self_update.rollback_intent import begin_rollback

    client, headers, store = api
    request = _request()
    store.create(request)
    previous = dict(schema=1, candidate_sha="1" * 40, attempt=1, worker_pid=123,
                    verified_at=time.time(), checks={name: True for name in SYSTEM_CHECKS})
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(request.update_id, phase, detail={"previous_system_gate": previous})
    begin_rollback(store, request.update_id, "system probe failed: health")
    restored = dict(previous, verified_at=time.time())
    store.transition(request.update_id, UpdatePhase.ROLLED_BACK,
                     detail={"previous_system_gate": previous, "restored_system_gate": restored})
    proof = store.root / request.update_id / "rollback-1.json"
    proof.chmod(mode)
    before = proof.read_bytes(), proof.stat().st_mtime_ns, proof.stat().st_mode

    response = client.get("/api/self-updates/su_test?session_id=session-1", headers=headers)

    assert response.status_code == (409 if mode & 0o077 else 200)
    if not mode & 0o077:
        assert response.json()["last_verified_runtime"]["candidate_sha"] == previous["candidate_sha"]
        assert response.json()["last_verified_runtime"]["source"] == "restored_system_gate"
    else:
        assert str(store.root) not in response.text
    assert (proof.read_bytes(), proof.stat().st_mtime_ns, proof.stat().st_mode) == before


@pytest.mark.parametrize("target", [None, "su_test", "su_missing"])
def test_terminal_history_validates_active_pointer_without_repair(api, monkeypatch, target):
    client, headers, store = api
    monkeypatch.setattr(running, "_collect", lambda: [])
    store.create(_request())
    store.transition("su_test", UpdatePhase.ABORTED)
    pointer = store.root / "active.json"
    if target is not None:
        store._write_json(pointer, {"schema": 1, "update_id": target})
    before = (pointer.read_bytes(), pointer.stat().st_mtime_ns) if pointer.exists() else None

    snapshot = client.get("/api/running", headers=headers)
    history = client.get("/api/self-updates?session_id=session-1", headers=headers)

    assert snapshot.status_code == 200
    assert snapshot.json()["items"] == []
    assert (snapshot.json()["self_update_error"] is not None) == (target == "su_missing")
    assert history.status_code == (409 if target == "su_missing" else 200)
    if target != "su_missing":
        assert history.json()["items"][0]["phase"] == "aborted"
    assert ((pointer.read_bytes(), pointer.stat().st_mtime_ns) if pointer.exists() else None) == before


@pytest.mark.parametrize("token", [17, None, {"private": "DO_NOT_EXPOSE"}])
def test_malformed_verifier_token_has_finite_public_errors(api, monkeypatch, token):
    client, headers, store = api
    monkeypatch.setattr(running, "_collect", lambda: [])
    store.create(_request())
    directory = store.root / "su_test"
    store._write_json(directory / "verifier-grant-1.json", {"token": token})
    store._write_json(directory / "verifier-result-1.json", {"signature": "0" * 64})
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir() if p.is_file()}

    detail = client.get("/api/self-updates/su_test?session_id=session-1", headers=headers)
    snapshot = client.get("/api/running", headers=headers)

    assert detail.status_code == 409
    assert snapshot.status_code == 200
    assert snapshot.json()["self_update_error"] is not None
    assert "DO_NOT_EXPOSE" not in detail.text + snapshot.text
    assert str(store.root) not in detail.text + snapshot.text
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir() if p.is_file()} == before
