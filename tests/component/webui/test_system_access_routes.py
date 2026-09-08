from fastapi import FastAPI
from fastapi.testclient import TestClient
from openprogram.webui.routes import misc, self_updates
from openprogram import system_access
from openprogram.self_update.projection import ProjectionAccessError
import openprogram.execution as execution_module
from openprogram.execution import AttemptStore, CapabilitySet, ExecutionStore
from openprogram.execution.waits import DurableWaitStore
from openprogram.agent.authority import owner_authority


def test_status_never_requests_and_remote_setup_is_denied(monkeypatch):
    app = FastAPI()
    misc.register(app)
    monkeypatch.setattr(system_access, 'report', lambda: {'capabilities': []})
    requested = []
    monkeypatch.setattr(system_access, 'request_access', lambda cap: requested.append(cap) or {'status': 'granted'})
    monkeypatch.setattr(self_updates, 'require_owner', lambda request: None)
    with TestClient(app, client=('203.0.113.4', 4000)) as client:
        assert client.get('/api/system/access').json() == {'capabilities': []}
        assert client.post('/api/system/access/accessibility').status_code == 403
    assert requested == []
    with TestClient(app, base_url='http://127.0.0.1:18100', headers={'origin': 'http://127.0.0.1:18100'}, client=('127.0.0.1', 4000)) as client:
        assert client.post('/api/system/access/accessibility').json()['status'] == 'granted'
    assert requested == ['accessibility']


def test_non_owner_cannot_prompt(monkeypatch):
    app = FastAPI()
    misc.register(app)
    def deny(request):
        raise ProjectionAccessError('denied')
    monkeypatch.setattr(self_updates, 'require_owner', deny)
    with TestClient(app, base_url='http://127.0.0.1:18100', headers={'origin': 'http://127.0.0.1:18100'}, client=('127.0.0.1', 4000)) as client:
        assert client.post('/api/system/access/accessibility').status_code == 403


def test_reverse_proxy_cannot_request_system_permission(monkeypatch):
    app = FastAPI()
    misc.register(app)
    monkeypatch.setattr(self_updates, 'require_owner', lambda request: None)
    calls = []
    monkeypatch.setattr(system_access, 'request_access', lambda cap: calls.append(cap))
    with TestClient(app, base_url='https://remote.example', client=('127.0.0.1', 4000)) as client:
        assert client.post('/api/system/access/accessibility', headers={
            'origin': 'https://remote.example', 'x-forwarded-for': '203.0.113.4'}).status_code == 403
    with TestClient(app, base_url='http://127.0.0.1:18100', client=('127.0.0.1', 4000)) as client:
        assert client.post('/api/system/access/accessibility').status_code == 403
    assert calls == []


def test_system_access_waits_are_scoped_and_not_questions(monkeypatch, tmp_path):
    store = ExecutionStore(tmp_path / "execution.db")
    revision = store.create_revision(manifest={"entrypoint": "agent"})
    execution = store.create_execution(
        execution_id="exec-system-route", run_id="run-system-route",
        session_id="session-system-route", revision_id=revision.revision_id,
        capabilities=CapabilitySet(pause=True),
    )
    attempt_store = AttemptStore(store)
    leased, reserved = attempt_store.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="route-test", ttl_seconds=30,
    )
    attempt_store.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    DurableWaitStore(store).open_wait(
        wait_id="wait-system-route", execution_id=execution.execution_id,
        attempt_id=leased.attempt_id, generation=leased.generation,
        kind="system_access", request={
            "required_capabilities": ["screen_recording"],
            "capabilities": [{"id": "screen_recording", "status": "not_granted"}],
        }, policy_snapshot={"version": 1, "kind": "system_access", "on_grant": "continue"},
        expires_at=0,
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    monkeypatch.setattr(
        "openprogram.webui.routes.lifecycle._actor_and_session",
        lambda request: (owner_authority("owner/install/0123456789abcdef"), None),
    )
    monkeypatch.setattr(
        "openprogram.webui.routes.lifecycle._authorize_read",
        lambda *args, **kwargs: True,
    )
    app = FastAPI()
    app.state.owner_auth = type("OwnerAuth", (), {
        "authority": owner_authority("owner/install/0123456789abcdef"),
    })()
    misc.register(app)
    with TestClient(app) as client:
        response = client.get(
            "/api/system/access/waits",
            params={"session_id": "session-system-route"},
        )
        assert response.status_code == 200
        assert response.json()["waits"] == [{
            "wait_id": "wait-system-route", "kind": "system_access",
            "session_id": "session-system-route", "execution_id": "exec-system-route",
            "wait_generation": 0, "expected_version": store.get_execution(execution.execution_id).status_version,
            "required_capabilities": ["screen_recording"],
            "capabilities": [{"id": "screen_recording", "status": "not_granted"}],
            "expires_at": 0.0, "reason_code": "system_access_required",
        }]
