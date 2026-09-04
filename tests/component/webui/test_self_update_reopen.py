"""Authenticated reopen protocol over private updates and real session history."""
from dataclasses import replace
import time
from types import SimpleNamespace

import pytest

from openprogram.agent import authority
from openprogram.self_update import UpdatePhase
from openprogram.self_update.rollback_intent import RECOVERY_SECONDS
from openprogram.self_update.verification_channel import _digest
from openprogram.self_update.verifier_config import config_evidence, freeze_verifier_config
from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.webui.test_self_update_routes import api  # noqa: F401
from tests.unit.self_update.test_store import _request


@pytest.fixture
def reopening(api, store_fixture, monkeypatch):
    client, headers, store = api
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed"))
    request = replace(_request(), session_id="p1", origin_assistant_id="a1")
    config = freeze_verifier_config(request, SimpleNamespace(
        agent_id="default", profile_snapshot={"id": "default"},
        **authority.local_owner_authority()))
    request = replace(request, pre_update_evidence=(*request.pre_update_evidence, config_evidence(config)))
    store.create(request, verifier_config=config)
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(request.update_id, phase, detail={
            "previous_system_gate": {"candidate_sha": request.base_sha},
        })
    intent = dict(schema=1, update_id=request.update_id, candidate_sha=request.candidate_sha,
                  request_sha256=_digest(request.to_dict()), attempt=1, session_id="p1",
                  owner_principal_id=authority.owner_principal_id(), created_at=time.time(),
                  expires_at=request.created_at + request.timeout_seconds + RECOVERY_SECONDS,
                  startup_action="restore_if_open")
    # A controller-produced on-disk input lets the initial public RED be HTTP404,
    # not a missing-module import or a mocked resolver.
    store._write_json(store.root / request.update_id / "reopen-1.json", intent)
    return client, headers, store, store_fixture, intent


URL = "/api/self-updates/su_test/desktop-reopen"


def test_authenticated_resolve_is_read_only_and_not_a_verifier(reopening, monkeypatch):
    client, headers, store, sessions, intent = reopening
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn",
                        lambda *a, **k: pytest.fail("reopen must not dispatch"))
    before = {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    head = sessions._open("p1")[1].head_id

    response = client.get(URL, headers=headers)

    assert response.status_code == 200, response.text
    value = response.json()
    assert value == dict(schema=1, update_id="su_test", attempt=1, session_id="p1",
                         launch_kind="activation", status="pending", expires_at=intent["expires_at"],
                         reopen_id=_digest({"intent": intent, "rollback": None}))
    assert response.headers["cache-control"] == "no-store"
    assert client.get(URL, headers=headers).json() == value
    assert {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before
    assert sessions._open("p1")[1].head_id == head
    assert store.load("su_test").state.dispatch is None


def test_ack_is_exact_durable_and_idempotent(reopening, monkeypatch):
    from openprogram.self_update import SelfUpdateStore
    from openprogram.self_update.reopen import resolve_reopen

    client, headers, store, sessions, intent = reopening
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn",
                        lambda *a, **k: pytest.fail("ACK must not dispatch"))
    value = client.get(URL, headers=headers).json()
    body = {k: value[k] for k in ("session_id", "reopen_id")}
    state_before = store.load("su_test")
    assert client.post(URL + "/ack", headers=headers, json={**body, "session_id": "other"}).status_code == 409
    assert client.post(URL + "/ack", headers=headers, json={**body, "reopen_id": "0" * 64}).status_code == 409
    assert not list((store.root / "su_test").glob("reopen-ack-*.json"))

    result = client.post(URL + "/ack", headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "acknowledged"
    path = store.root / "su_test" / "reopen-ack-1-activation.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert path.stat().st_mode & 0o777 == 0o600
    # A fresh store simulates a lost HTTP response and process restart.
    assert resolve_reopen(SelfUpdateStore(store.root), update_id="su_test",
                          principal_id=intent["owner_principal_id"])["status"] == "acknowledged"
    assert client.post(URL + "/ack", headers=headers, json=body).json() == result.json()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert store.load("su_test") == state_before
    assert sessions._open("p1")[1].head_id == "a1"


def test_activation_ack_cannot_acknowledge_rollback(reopening):
    from openprogram.self_update.rollback_intent import begin_rollback

    client, headers, store, _, _ = reopening
    initial = client.get(URL, headers=headers).json()
    body = {k: initial[k] for k in ("session_id", "reopen_id")}
    assert client.post(URL + "/ack", headers=headers, json=body).status_code == 200
    begin_rollback(store, "su_test", "system check failed")
    value = client.get(URL, headers=headers).json()
    assert value["launch_kind"] == "rollback" and value["status"] == "pending"
    assert value["reopen_id"] != initial["reopen_id"]
    assert client.post(URL + "/ack", headers=headers, json=body).status_code == 409
    body["reopen_id"] = value["reopen_id"]
    assert client.post(URL + "/ack", headers=headers, json=body).status_code == 200
    assert len(list((store.root / "su_test").glob("reopen-ack-*.json"))) == 2
    assert store.load("su_test").state.phase is UpdatePhase.ACTIVATING
    assert store.load("su_test").state.dispatch is None


@pytest.mark.parametrize("method,path", [("get", URL), ("post", URL + "/ack")])
def test_reopen_requires_owner_authentication(reopening, method, path):
    client, headers, _, _, _ = reopening
    kwargs = {"json": {"session_id": "p1", "reopen_id": "0" * 64}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    assert getattr(client, method)(path, headers={"Authorization": "Bearer wrong"}, **kwargs).status_code == 401


@pytest.mark.parametrize("field,value", [
    ("owner_principal_id", "foreign"), ("session_id", "other"), ("update_id", "su_other"),
    ("candidate_sha", "3" * 40), ("request_sha256", "0" * 64), ("attempt", 2),
    ("schema", True), ("attempt", True), ("startup_action", "https://example.com"),
    ("expires_at", 1), ("created_at", -1),
])
def test_reopen_rejects_changed_intent_without_leaking_or_repair(reopening, field, value):
    client, headers, store, _, intent = reopening
    path = store.root / "su_test" / "reopen-1.json"
    store._write_json(path, {**intent, field: value})
    before = path.read_bytes(), path.stat().st_mtime_ns
    result = client.get(URL, headers=headers)
    assert result.status_code == 409
    assert result.json() == {"reason": "intent_invalid"}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert str(store.root) not in result.text


@pytest.mark.parametrize("case,reason", [
    ("session", "session_missing"), ("intent", "intent_missing"),
    ("expiry", "intent_expired"), ("owner", "state_invalid"), ("mode", "state_invalid"),
])
def test_missing_expired_and_foreign_owner_fall_back(reopening, monkeypatch, case, reason):
    client, headers, store, sessions, intent = reopening
    path = store.root / "su_test" / "reopen-1.json"
    if case == "session":
        sessions.delete_session("p1")
    elif case == "intent":
        path.unlink()
    elif case == "expiry":
        monkeypatch.setattr("openprogram.self_update.reopen.time.time", lambda: intent["expires_at"] + 1)
    elif case == "owner":
        monkeypatch.setattr(authority, "owner_principal_id", lambda: "new-owner")
    elif case == "mode":
        path.chmod(0o644)
    result = client.get(URL, headers=headers)
    assert result.status_code in (403, 409)
    assert result.json()["reason"] in ({reason, "owner_mismatch"} if case == "owner" else {reason})
    assert result.headers["cache-control"] == "no-store"
    assert store.load("su_test").state.dispatch is None
    if case == "session":
        assert sessions._open("p1") is None
        assert not sessions._session_dir("p1").exists()
    if case == "mode":
        assert path.stat().st_mode & 0o777 == 0o644


def test_ack_schema_rejects_navigation_and_extra_fields(reopening):
    client, headers, _, _, _ = reopening
    body = {"session_id": "p1", "reopen_id": "0" * 64}
    for extra in ({"url": "file:///private/data"}, {"token": "secret"}, {"reopen_id": True}):
        assert client.post(URL + "/ack", headers=headers, json={**body, **extra}).status_code == 422


def test_ack_write_failure_retries_without_changing_update(reopening, monkeypatch):
    from openprogram.self_update import SelfUpdateStore

    client, headers, store, _, _ = reopening
    value = client.get(URL, headers=headers).json()
    body = {k: value[k] for k in ("session_id", "reopen_id")}
    before = store.load("su_test")
    write = SelfUpdateStore._write_json
    with monkeypatch.context() as patch:
        def fail_ack(self, path, data):
            if path.name.startswith("reopen-ack-"):
                raise OSError("private path must not enter response")
            write(self, path, data)
        patch.setattr(SelfUpdateStore, "_write_json", fail_ack)
        response = client.post(URL + "/ack", headers=headers, json=body)
        assert response.status_code == 409 and response.json() == {"reason": "state_invalid"}
    assert client.get(URL, headers=headers).json()["status"] == "pending"
    assert client.post(URL + "/ack", headers=headers, json=body).status_code == 200
    assert store.load("su_test") == before


def test_symlink_intent_does_not_read_or_change_target(reopening, tmp_path):
    client, headers, store, _, _ = reopening
    target = tmp_path / "private-file"
    target.write_text('{"secret":"do not expose"}')
    path = store.root / "su_test" / "reopen-1.json"
    path.unlink()
    path.symlink_to(target)
    before = target.read_bytes(), target.stat().st_mtime_ns, target.stat().st_mode
    response = client.get(URL, headers=headers)
    assert response.status_code == 409 and response.json() == {"reason": "state_invalid"}
    assert (target.read_bytes(), target.stat().st_mtime_ns, target.stat().st_mode) == before
    assert path.is_symlink()


def test_controller_prepares_once_before_activation(api, store_fixture, monkeypatch):
    from openprogram.self_update.reopen import prepare_reopen

    client, headers, store = api
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed"))
    request = replace(_request(), session_id="p1", origin_assistant_id="a1")
    config = freeze_verifier_config(request, SimpleNamespace(
        profile_snapshot={}, **authority.local_owner_authority()))
    request = replace(request, pre_update_evidence=(*request.pre_update_evidence, config_evidence(config)))
    store.create(request, verifier_config=config)
    store.transition("su_test", UpdatePhase.STAGING)
    store.transition("su_test", UpdatePhase.READY)
    first = prepare_reopen(store, "su_test")
    path = store.root / "su_test" / "reopen-1.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert prepare_reopen(store, "su_test") == first
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert client.get(URL, headers=headers).json() == {"reason": "activation_not_started"}
    store.transition("su_test", UpdatePhase.ACTIVATING)
    assert client.get(URL, headers=headers).status_code == 200
    with pytest.raises(ValueError, match="activation_not_ready"):
        prepare_reopen(store, "su_test")
