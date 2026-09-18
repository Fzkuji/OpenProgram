"""Backend discovery and business invocation do not require a browser."""
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_describe_uses_registered_operations_and_command_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.webui.routes.control import framework
    app = FastAPI()
    @app.post('/api/example/{key}')
    def example(key: str, body: dict):
        return {'key': key, 'value': body}
    framework.register(app)
    with TestClient(app) as client:
        value = client.get('/api/framework/operations', params={'query': 'example'}).json()
        assert value['operations'][0]['path'] == '/api/example/{key}'
        assert value['operations'][0]['requestBody']
        commands = client.get('/api/framework/commands').json()['commands']
        assert any(row['name'] == 'rename_session' for row in commands)
        assert not any(row['name'] == 'webtab_result' for row in commands)
        result = client.post('/api/framework/commands/get_settings', json={})
        assert result.status_code == 200, result.text
        assert any(frame['type'] == 'settings' for frame in result.json()['frames'])
        assert client.post('/api/framework/commands/webtab_result', json={}).status_code == 400
