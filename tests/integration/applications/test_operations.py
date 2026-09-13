"""Application API lifecycle with a real isolated backend process."""
import json
from tests.support.waiting import wait_until

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_operation_state_question_deduplication_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from openprogram.webui.routes.catalog import applications

    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("<!doctype html><title>Notes</title>")
    (source / "backend.py").write_text('''
def save(value, context):
    current = context.load()
    return context.save(value, expected_version=current['version'])

def confirm(value, context):
    return context.ask('Continue?')

operations = {'save': save, 'confirm': confirm}
''')
    (source / "application.json").write_text(json.dumps({
        "id": "test.notes", "title": "Notes", "version": "1",
        "capabilities": ["storage.app"],
        "backend": {"kind": "python", "entry": "backend:operations"},
        "operations": {"save": {"input": {"type": "object"}}, "confirm": {}},
    }))
    app = FastAPI()
    applications.register(app)
    from openprogram.webui.routes.execution import lifecycle
    from openprogram.agent.authority import owner_authority, owner_principal_id
    from types import SimpleNamespace
    lifecycle.register(app)
    app.state.owner_auth = SimpleNamespace(authority=owner_authority(owner_principal_id()))
    with TestClient(app) as client:
        install = client.post('/api/applications/install', json={"path": str(source), "trust": True})
        assert install.status_code == 200, install.text
        definition = install.json()
        opened = client.post('/api/applications/test.notes/open', json={}).json()
        base = '/api/application-instances/' + opened['instance_id']

        def submit(operation, key, value=None):
            response = client.post(base + '/operations/' + operation, json={
                'digest': definition['digest'], 'request_key': key, 'input': value or {},
            })
            assert response.status_code == 200, response.text
            return response.json()

        def wait(run_id, predicate):
            run = None
            def ready():
                nonlocal run
                response = client.get('/api/application-runs/' + run_id)
                assert response.status_code == 200, response.text
                run = response.json()
                return predicate(run)
            assert wait_until(ready, timeout=15, interval=.02), run
            return run

        saved = submit('save', 'save-1', {'note': 'retained'})
        completed = wait(saved['id'], lambda run: run['status'] in {'completed', 'failed'})
        assert completed['status'] == 'completed', completed
        assert client.get(base + '/state').json()['value'] == {'note': 'retained'}
        assert submit('save', 'save-1', {'note': 'retained'})['id'] == saved['id']
        waiting = submit('confirm', 'question-1')
        question = wait(waiting['id'], lambda run: run['question'] is not None)
        answer = client.post('/api/application-runs/' + waiting['id'] + '/answer', json={
            'request_id': question['question']['request_id'], 'answer': 'yes',
        })
        assert answer.status_code == 200, answer.text
        assert wait(waiting['id'], lambda run: run['status'] == 'completed')['result'] == 'yes'
        cancelled = submit('confirm', 'question-2')
        response = client.post('/api/application-runs/' + cancelled['id'] + '/cancel')
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'cancelled'

        generic = submit('confirm', 'generic-cancel')
        pending = wait(generic['id'], lambda run: run['question'] is not None)
        process = app.state.applications.processes[generic['id']]
        from openprogram.execution import default_store
        record = default_store().get_execution(generic['id'])
        response = client.post('/api/execution/cancel', json={
            'type': 'execution.command', 'action': 'execution.cancel', 'command_id': 'generic-cancel-command',
            'execution_id': generic['id'], 'expected_version': record.status_version, 'payload': {},
        })
        assert response.status_code == 200, response.text
        cancelled = wait(generic['id'], lambda run: run['status'] == 'cancelled')
        assert process.returncode is not None
        assert cancelled['question'] is None
        assert client.post('/api/application-runs/' + generic['id'] + '/answer', json={
            'request_id': pending['question']['request_id'], 'answer': 'too late',
        }).status_code == 400
        assert default_store().get_command('generic-cancel-command').status.value == 'applied'
        interrupted = submit('confirm', 'shutdown-1')
        wait(interrupted['id'], lambda run: run['question'] is not None)
    # The view/HTTP client is not the supervisor; worker shutdown is.
    restarted = FastAPI()
    applications.register(restarted)
    with TestClient(restarted) as client:
        assert client.get('/api/application-runs/' + interrupted['id']).json()['status'] == 'interrupted'
        assert client.get(base + '/state').json()['value'] == {'note': 'retained'}


def test_model_runtime_binding_and_failure_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from openprogram.webui.routes.catalog import applications
    source = tmp_path / 'model-source'
    source.mkdir()
    (source / 'index.html').write_text('<!doctype html><title>Model</title>')
    (source / 'backend.py').write_text('''
from openprogram.providers import registry
from openprogram.agentic_programming.runtime import Runtime
from openprogram.agentic_programming import llm

def create_runtime(model=None):
    assert model == 'application-test-model'
    def call(content, **kwargs):
        if 'fail' in str(content):
            raise ValueError('fixture model failure')
        return 'fixture summary'
    return Runtime(call=call, model=model, max_retries=1)
registry.create_runtime = create_runtime

def summarize(value, context):
    return llm(value['text'])
operations = {'summarize': summarize}
''')
    (source / 'application.json').write_text(json.dumps({
        'id': 'test.model', 'title': 'Model', 'version': '1',
        'model': 'application-test-model', 'capabilities': ['model.invoke'],
        'backend': {'kind': 'python', 'entry': 'backend:operations'},
        'operations': {'summarize': {'agent': True, 'input': {'type': 'object'}, 'output': {'type': 'string'}}},
    }))
    app = FastAPI()
    applications.register(app)
    with TestClient(app) as client:
        response = client.post('/api/applications/install', json={'path': str(source), 'trust': True})
        assert response.status_code == 200, response.text
        opened = client.post('/api/applications/test.model/open', json={}).json()
        for text, expected in [('paper text', 'completed'), ('fail', 'failed')]:
            response = client.post('/api/application-instances/' + opened['instance_id'] + '/operations/summarize', json={
                'digest': opened['application']['digest'], 'input': {'text': text}, 'request_key': text, 'agent': True,
            })
            assert response.status_code == 200, response.text
            run_id = response.json()['id']
            current = {}
            def finished():
                current.update(client.get('/api/application-runs/' + run_id).json())
                return current.get('status') in {'completed', 'failed'}
            assert wait_until(finished, timeout=15), current
            assert current['status'] == expected, current
            if expected == 'completed':
                assert current['result'] == 'fixture summary'
                history = tmp_path / '.openprogram' / 'applications' / 'runs' / run_id / 'history'
                assert list(history.glob('*.json')), 'model calls must retain their framework trace'
            else:
                assert 'fixture model failure' in current['error']


def test_install_from_named_desktop_runtime(tmp_path, monkeypatch):
    """A native worker executable is not the interpreter for app environments."""
    import os
    import sys
    import shlex
    import pytest
    from openprogram.webui.routes.catalog import applications

    monkeypatch.setenv('HOME', str(tmp_path))
    runtime = tmp_path / 'runtime'
    if os.name == 'nt':
        pytest.skip('macOS named runtime uses a POSIX executable')
    runtime.mkdir()
    interpreter = runtime / 'python'
    interpreter.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
    interpreter.chmod(0o755)
    wrapper = runtime / 'OpenProgram'
    wrapper.write_text('not a Python interpreter')
    wrapper.chmod(0o755)
    (runtime / 'bin').mkdir()
    (runtime / 'bin/verify-product-runtime.py').write_text('# verifier')
    (runtime / 'product-runtime.json').write_text('{}')
    (runtime / 'runtime-manifest.json').write_text(json.dumps({
        'schema': 2, 'python': str(interpreter.relative_to(runtime)),
        'worker_python': 'OpenProgram',
    }))
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'index.html').write_text('<!doctype html><title>Native runtime</title>')
    (source / 'backend.py').write_text('operations = {}')
    (source / 'application.json').write_text(json.dumps({
        'id': 'test.native', 'title': 'Native runtime', 'version': '1',
        'backend': {'kind': 'python', 'entry': 'backend:operations'},
    }))
    monkeypatch.setattr(sys, 'executable', str(wrapper))
    monkeypatch.setattr(sys, '_base_executable', str(wrapper))
    app = FastAPI()
    applications.register(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/api/applications/install', json={'path': str(source), 'trust': True})
        assert response.status_code == 200, response.text
        assert response.json()['id'] == 'test.native'
