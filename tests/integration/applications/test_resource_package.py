"""Scaffold, install and operate a user package without a renderer."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.waiting import wait_until


def test_scaffold_install_and_resource_operation(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.webui.routes.catalog import applications
    from openprogram.webui.routes.control import resources
    app = FastAPI()
    applications.register(app)
    resources.register(app)
    with TestClient(app) as client:
        path = str(tmp_path / 'notes')
        assert client.post('/api/applications/scaffold', json={'path': path, 'app_id': 'test.notes', 'title': 'Notes'}).status_code == 200
        assert client.post('/api/applications/scaffold', json={'path': path, 'app_id': 'test.notes', 'title': 'No overwrite'}).status_code == 400
        response = client.post('/api/applications/install', json={'path': path, 'trust': True})
        assert response.status_code == 200, response.text
        descriptor = next(p for p in client.get('/api/resources/providers').json()['providers'] if p['provider'] == 'application.test.notes')
        assert len(descriptor['actions']['act']['oneOf']) == 2
        base = '/api/resources/application.test.notes/'
        opened = client.post(base+'open', json={'session_id': 'chat'}).json()
        identity = {k: opened[k] for k in ('instance_id', 'digest')}
        request = {**identity, 'operation': 'save', 'input': {'text': 'same backend state', 'version': 0}, 'request_key': 'once'}
        run = client.post(base+'act', json=request).json()
        result = None
        def done():
            nonlocal result
            result = client.post(base+'status', json={**identity, 'run_id': run['id']}).json()
            return result['status'] in {'completed', 'failed'}
        assert wait_until(done, timeout=15, interval=.02), result
        assert result['status'] == 'completed', result
        assert client.post(base+'act', json=request).json()['id'] == run['id']
        observed = client.post(base+'observe', json=identity).json()
        assert observed['state']['value']['text'] == 'same backend state'
        assert client.get('/api/application-instances/'+identity['instance_id']+'/state').json() == observed['state']
        assert client.post(base+'act', json={**request, 'digest': 'old'}).status_code == 400
        assert client.post(base+'save', json={**identity, 'value': {}, 'version': 0}).status_code == 400
        assert client.delete('/api/applications/test.notes').status_code == 200
        assert client.post(base+'observe', json=identity).status_code == 404
