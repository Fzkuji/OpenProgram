"""The public framework tool cannot turn a UI-only operation into an Agent operation."""
import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_framework_preserves_application_agent_exposure(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.programs._applications.scaffold import create
    from openprogram.webui.routes.catalog import applications
    from openprogram.webui.routes.control import framework as routes
    from openprogram.programs.tools.runtime.framework import framework
    from openprogram.agent import turn_request_context
    source = tmp_path / 'notes'
    create(str(source), 'test.private', 'Private')
    manifest = source / 'application.json'
    definition = json.loads(manifest.read_text())
    definition['operations']['save']['agent'] = False
    manifest.write_text(json.dumps(definition))
    app = FastAPI()
    applications.register(app)
    routes.register(app)
    with TestClient(app) as client:
        installed = client.post('/api/applications/install', json={'path': str(source), 'trust': True})
        assert installed.status_code == 200, installed.text
        opened = client.post('/api/applications/test.private/open', json={}).json()
        class Proxy:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def request(self, method, url, **kwargs):
                return client.request(method, url, **kwargs)
        monkeypatch.setattr('httpx.Client', lambda **_kwargs: Proxy())
        monkeypatch.setattr('openprogram.backend_endpoint.resolve_backend_endpoint', lambda: SimpleNamespace(
            base_url='http://testserver', authorization_header='Bearer test', origin='http://testserver'))
        monkeypatch.setattr('openprogram.backend.get_active_backend', lambda: SimpleNamespace(backend_id='local'))
        monkeypatch.setattr('openprogram.sandbox.resolve_policy', lambda: None)
        token = turn_request_context.set_turn_request(SimpleNamespace(authority_tier='owner'))
        try:
            for extra in ({}, {'agent': False}):
                result = asyncio.run(framework.execute('exposure', {'action': 'invoke',
                    'operation': 'POST /api/application-instances/{key}/operations/{operation}',
                    'arguments': {'path': {'key': opened['instance_id'], 'operation': 'save'},
                        'body': {'digest': opened['application']['digest'], 'input': {'text': 'denied', 'version': 0},
                                 'request_key': 'denied', **extra}}}, None, None))
                assert result.is_error
            assert client.get('/api/application-instances/' + opened['instance_id'] + '/state').json()['version'] == 0
        finally:
            turn_request_context.reset_turn_request(token)
