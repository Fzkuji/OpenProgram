from fastapi import FastAPI
from fastapi.testclient import TestClient
from openprogram.webui.routes import misc, self_updates
from openprogram import system_access
from openprogram.self_update.projection import ProjectionAccessError


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
