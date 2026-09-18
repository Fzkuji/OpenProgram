"""An installed application becomes a resource without registration or a view."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_resource_discovery_session_binding_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.webui.routes.catalog import applications
    from openprogram.webui.routes.control import resources
    from openprogram.resources import registry
    source = tmp_path / 'software'
    source.mkdir()
    (source / 'index.html').write_text('<h1>Resource</h1>')
    (source / 'application.json').write_text(json.dumps({
        'id': 'test.resource', 'title': 'Resource', 'version': '1', 'capabilities': ['storage.app'],
    }))
    app = FastAPI()
    applications.register(app)
    resources.register(app)
    with TestClient(app) as client:
        assert client.post('/api/applications/install', json={'path': str(source)}).status_code == 200
        descriptor = registry.describe('application.test.resource')
        assert 'open' in descriptor['actions']
        base = '/api/resources/application.test.resource/'
        opened = client.post(base + 'open', json={'session_id': 'session-a'}).json()
        assert opened['instance_id']
        from openprogram.resources.application_bindings import list_bindings
        assert list_bindings('session-a')[0]['application_instance_id'] == opened['instance_id']
        assert list_bindings('session-b') == []
        observe = {'instance_id': opened['instance_id'], 'digest': opened['digest']}
        assert client.post(base + 'observe', json=observe).json()['state']['version'] == 0
        assert client.post(base + 'release', json={**observe, 'session_id': 'session-a'}).status_code == 200
        assert list_bindings('session-a') == []
        assert client.post(base + 'observe', json=observe).status_code == 200
        assert client.patch('/api/applications/test.resource', json={'enabled': False}).status_code == 200
        assert 'application.test.resource' not in [p['provider'] for p in registry.describe()['providers']]
        assert client.post(base + 'observe', json=observe).status_code == 404
