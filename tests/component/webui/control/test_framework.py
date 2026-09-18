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


def test_framework_cannot_change_its_own_approval_policy():
    from openprogram.webui.routes.control import framework
    app = FastAPI()
    framework.register(app)
    with TestClient(app) as client:
        names = {item['name'] for item in client.get('/api/framework/commands').json()['commands']}
        for name in ('set_permission', 'add_permission_rule', 'remove_permission_rule'):
            assert name not in names
            assert client.post('/api/framework/commands/' + name, json={'mode': 'bypass'}).status_code == 400
        assert client.post('/api/framework/commands/set_project_config', json={'key': 'permission_mode', 'value': 'bypass'}).status_code == 400
        assert client.post('/api/framework/commands/chat', json={'permission_mode': 'bypass'}).status_code == 400


def test_framework_reports_canonical_command_failures(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.webui.routes.control import framework
    app = FastAPI()
    framework.register(app)
    with TestClient(app) as client:
        from openprogram.programs.tools.runtime.framework import framework as tool
        monkeypatch.setattr('openprogram.framework.client.require_owner', lambda: None)
        monkeypatch.setattr('openprogram.programs.application_client.request',
                            lambda path, method='GET', body=None: client.request(method, path, json=body).json())
        for name, body in [('set_working_dirs', {}), ('set_setting', {'key': 'not_a_real_setting', 'value': 0})]:
            result = client.post('/api/framework/commands/' + name, json=body).json()
            assert result['ok'] is False
            assert result['frames']
            result = asyncio.run(tool.execute('error', {'action': 'command', 'operation': name, 'arguments': body}, None, None))
            assert result.is_error
        assert not asyncio.run(tool.execute('success', {'action': 'command', 'operation': 'get_settings'}, None, None)).is_error
