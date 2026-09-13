"""An open application follows project identity, not a stale directory name."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.waiting import wait_until


def test_open_application_follows_project_moves_and_rejects_replacements(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.store.project import resolve_project, relocate_project
    from openprogram.webui.routes.catalog import applications

    folder = tmp_path / 'project'
    folder.mkdir()
    (folder / 'value.txt').write_text('bound project')
    project = resolve_project(folder)
    source = tmp_path / 'application'
    source.mkdir()
    (source / 'index.html').write_text('<!doctype html><title>Read</title>')
    (source / 'backend.py').write_text('''
def read(value, context):
    if value.get('wait'):
        context.ask('Read now?')
    return context.read_file('value.txt')
operations = {'read': read}
''')
    (source / 'application.json').write_text(json.dumps({
        'id': 'test.location', 'title': 'Read', 'version': '1', 'scope': 'project',
        'capabilities': ['files.project.read', 'storage.app'],
        'backend': {'kind': 'python', 'entry': 'backend:operations'}, 'operations': {'read': {}},
    }))
    app = FastAPI()
    applications.register(app)
    with TestClient(app) as client:
        installed = client.post('/api/applications/install', json={'path': str(source), 'trust': True})
        assert installed.status_code == 200, installed.text
        opened = client.post('/api/applications/test.location/open', json={'project_id': project.id}).json()
        base = '/api/application-instances/' + opened['instance_id']
        assert client.put(base + '/state', json={'value': {'note': 'retained'}, 'version': 0}).status_code == 200

        def submit(key, value=None):
            return client.post(base + '/operations/read', json={
                'digest': installed.json()['digest'], 'request_key': key, 'input': value or {},
            })

        def wait(run_id, predicate):
            result = {}
            def ready():
                result.update(client.get('/api/application-runs/' + run_id).json())
                return predicate(result)
            assert wait_until(ready, timeout=15), result
            return result

        moved = tmp_path / 'moved'
        folder.rename(moved)
        relocate_project(project.id, moved)
        folder.mkdir()
        (folder / 'value.txt').write_text('unrelated old path')
        run = submit('after-move')
        assert run.status_code == 200, run.text
        result = wait(run.json()['id'], lambda r: r['status'] in {'completed', 'failed'})
        assert result['status'] == 'completed', result
        assert result['result'] == 'bound project'
        assert client.get(base).json()['instance']['project_path'] == str(moved)
        assert client.get(base + '/state').json()['value'] == {'note': 'retained'}

        waiting = submit('move-while-waiting', {'wait': True}).json()
        pending = wait(waiting['id'], lambda r: r['question'] is not None)
        relocated = tmp_path / 'relocated'
        moved.rename(relocated)
        relocate_project(project.id, relocated)
        moved.mkdir()
        (moved / 'value.txt').write_text('unrelated pending path')
        answered = client.post('/api/application-runs/' + waiting['id'] + '/answer', json={
            'request_id': pending['question']['request_id'], 'answer': 'read',
        })
        assert answered.status_code == 200, answered.text
        result = wait(waiting['id'], lambda r: r['status'] in {'completed', 'failed'})
        assert result['status'] == 'completed', result
        assert result['result'] == 'bound project'

        # The same name now refers to a different directory identity. No new
        # execution may read it, but saved application data stays available.
        relocated.rename(tmp_path / 'disconnected')
        relocated.mkdir()
        (relocated / 'value.txt').write_text('replacement')
        blocked = submit('replacement')
        assert blocked.status_code == 400, blocked.text
        assert 'project' in blocked.json()['error'].lower()
        assert client.get(base + '/state').json()['value'] == {'note': 'retained'}
