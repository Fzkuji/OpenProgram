"""Public application routes retain authorization and physical process ownership."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.waiting import wait_until


def source_package(tmp_path, backend):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'index.html').write_text('<!doctype html>')
    (source / 'backend.py').write_text(backend)
    definition = {'id': 'test.ownership', 'title': 'Ownership', 'version': '1',
                  'backend': {'kind': 'python', 'entry': 'backend:operations'},
                  'operations': {'run': {}}}
    (source / 'application.json').write_text(json.dumps(definition))
    return source, definition


def application():
    from openprogram.webui.routes import applications
    app = FastAPI()
    applications.register(app)
    return app


def start(client, source):
    installed = client.post('/api/applications/install', json={'path': str(source), 'trust': True})
    assert installed.status_code == 200, installed.text
    opened = client.post('/api/applications/test.ownership/open', json={}).json()
    response = client.post('/api/application-instances/' + opened['instance_id'] + '/operations/run',
                           json={'digest': installed.json()['digest'], 'request_key': 'run'})
    assert response.status_code == 200, response.text
    return response.json()['id']


def test_snapshot_cannot_add_untrusted_backend(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.programs._applications import catalog
    marker = tmp_path / 'imported'
    source, definition = source_package(tmp_path,
        f'from pathlib import Path\nPath({str(marker)!r}).touch()\noperations = {{"run": lambda v,c: None}}\n')
    (source / 'application.json').write_text(json.dumps({k: v for k, v in definition.items()
                                                       if k not in {'backend', 'operations'}}))
    original = catalog.shutil.copyfile
    def copy(src, dst, *args, **kwargs):
        if Path(src).name == 'application.json':
            Path(src).write_text(json.dumps(definition))
        return original(src, dst, *args, **kwargs)
    monkeypatch.setattr(catalog.shutil, 'copyfile', copy)
    with TestClient(application()) as client:
        response = client.post('/api/applications/install', json={'path': str(source)})
        assert response.status_code == 400, response.text
        assert 'trust' in response.text
        assert not marker.exists()
        assert catalog.installed() == []


def test_concurrent_cancel_waits_for_physical_termination(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram import _compat
    source, _ = source_package(tmp_path, 'operations = {"run": lambda v,c: c.ask("Continue?")}\n')
    app = application()
    with TestClient(app) as client:
        run_id = start(client, source)
        url = '/api/application-runs/' + run_id
        assert wait_until(lambda: client.get(url).json()['question'], timeout=15)
        question = client.get(url).json()['question']
        process = app.state.applications.processes[run_id]
        killing, release = Event(), Event()
        old_kill, old_terminate = _compat.kill_process_tree, _compat.ProcessTreeOwner.terminate
        def hold(call, *args):
            killing.set()
            assert release.wait(10)
            return call(*args)
        monkeypatch.setattr(_compat, 'kill_process_tree', lambda pid: hold(old_kill, pid))
        monkeypatch.setattr(_compat.ProcessTreeOwner, 'terminate', lambda owner: hold(old_terminate, owner))
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(client.post, url + '/cancel')
            try:
                assert killing.wait(5)
                second = pool.submit(client.post, url + '/cancel')
                assert not wait_until(lambda: first.done() or second.done(), timeout=.3, interval=.01)
                assert process.returncode is None
                assert client.get(url).json()['status'] == 'cancelling'
            finally:
                release.set()
            for future in (first, second):
                response = future.result(timeout=10)
                assert response.status_code == 200, response.text
                assert response.json()['status'] == 'cancelled'
                assert response.json()['question'] is None
        assert process.returncode is not None
        assert run_id not in app.state.applications.processes
        assert run_id not in app.state.applications.tasks
        assert client.post(url + '/answer', json={'request_id': question['request_id'], 'answer': 'late'}).status_code == 400


def test_cancel_owns_descendants_after_leader_exit(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    trigger, marker, pidfile = (tmp_path / name for name in ('trigger', 'marker', 'pid'))
    child = (f'import os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\n'
             f'while not Path({str(trigger)!r}).exists(): time.sleep(.01)\n'
             f'Path({str(marker)!r}).write_text("post-cancel write")\n')
    source, _ = source_package(tmp_path,
        f'import subprocess,sys\ndef run(v,c):\n subprocess.Popen([sys.executable,"-c",{child!r}])\n return "done"\noperations={{"run":run}}\n')
    app = application()
    with TestClient(app) as client:
        run_id = start(client, source)
        url = '/api/application-runs/' + run_id
        assert wait_until(lambda: run_id in app.state.applications.processes, timeout=15)
        process = app.state.applications.processes[run_id]
        try:
            assert wait_until(lambda: pidfile.exists() and process.returncode is not None, timeout=15)
            assert process.returncode == 0
            assert client.get(url).json()['status'] == 'running'
            response = client.post(url + '/cancel')
            assert response.status_code == 200, response.text
            assert response.json()['status'] == 'cancelled'
            trigger.touch()
            assert not wait_until(marker.exists, timeout=.5, interval=.01)
        finally:
            # Release a surviving fixture child even on the pre-fix candidate.
            trigger.touch()
