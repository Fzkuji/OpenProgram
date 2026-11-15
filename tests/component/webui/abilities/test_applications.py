from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_application_register_open_and_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from openprogram.interfaces.server.routes.catalog import applications

    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("<h1>Calculator</h1>")
    (source / "application.json").write_text(json.dumps({
        "id": "local.calculator", "title": "Calculator", "version": "1.0.0",
        "ui": {"root": ".", "entry": "index.html"}, "scope": "global",
    }))
    app = FastAPI()
    applications.register(app)
    with TestClient(app) as client:
        installed = client.post("/api/applications/install", json={"path": str(source)})
        assert installed.status_code == 200, installed.text
        assert client.get("/api/applications").json()["applications"][0]["id"] == "local.calculator"
        opened = client.post("/api/applications/local.calculator/open", json={}).json()
        assert opened["instance_id"]
        html = client.get(opened["ui_url"])
        assert "Calculator" in html.text
        assert "sandbox allow-scripts" in html.headers["content-security-policy"]
        # The UI is served from the source directory: nothing was copied.
        assert not (tmp_path / ".openprogram" / "applications" / "versions").exists()
        (source / "index.html").write_text("changed in the source")
        assert "changed in the source" in client.get(opened["ui_url"]).text
        assert client.delete("/api/applications/local.calculator").status_code == 200
        assert client.get(opened["ui_url"]).status_code == 404


def test_upgrade_rejects_schema_changes_and_storage_conflicts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from openprogram.interfaces.server.routes.catalog import applications
    source = tmp_path / 'versioned'
    source.mkdir()
    definition = {'id': 'test.versioned', 'title': 'Versioned', 'version': '1', 'capabilities': ['storage.app']}
    (source / 'index.html').write_text('<!doctype html><h1>Original</h1>')
    (source / 'application.json').write_text(json.dumps(definition))
    app = FastAPI()
    applications.register(app)
    with TestClient(app) as client:
        assert client.post('/api/applications/install', json={'path': str(source)}).status_code == 200
        opened = client.post('/api/applications/test.versioned/open', json={}).json()
        state_url = '/api/application-instances/' + opened['instance_id'] + '/state'
        assert client.put(state_url, json={'value': {'note': 'preserved'}, 'version': 0}).status_code == 200
        assert client.put(state_url, json={'value': {'note': 'lost'}, 'version': 0}).status_code == 400
        definition['dataSchema'] = 2
        (source / 'application.json').write_text(json.dumps(definition))
        assert client.post('/api/applications/install', json={'path': str(source), 'replace': True}).status_code == 400
        assert 'Original' in client.get(opened['ui_url']).text
        assert client.get(state_url).json()['value'] == {'note': 'preserved'}
        assert client.patch('/api/applications/test.versioned', json={'enabled': False}).status_code == 200
        assert client.get(opened['ui_url']).status_code == 404
        assert client.post('/api/applications/test.versioned/open', json={}).status_code == 404

        assert client.delete('/api/applications/test.versioned').status_code == 200
        rejected = client.post('/api/applications/install', json={'path': str(source), 'replace': True})
        assert rejected.status_code == 400
        assert 'migration' in rejected.json()['error']
        definition['dataSchema'] = 1
        definition['scope'] = 'project'
        (source / 'application.json').write_text(json.dumps(definition))
        rejected = client.post('/api/applications/install', json={'path': str(source), 'replace': True})
        assert rejected.status_code == 400
        assert 'migration' in rejected.json()['error']
        definition['scope'] = 'global'
        (source / 'application.json').write_text(json.dumps(definition))
        assert client.post('/api/applications/install', json={'path': str(source), 'replace': True}).status_code == 200
        restored = client.post('/api/applications/test.versioned/open', json={}).json()
        assert restored['instance_id'] == opened['instance_id']
        assert client.get(state_url).json()['value'] == {'note': 'preserved'}


def test_application_environments_seed_from_the_real_python_behind_a_named_launcher(tmp_path, monkeypatch):
    import sys
    from openprogram.programs._host.ui import catalog
    from openprogram.operations.self_update.github_release import detect

    base = tmp_path / "python"
    real = base / "bin" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    real.parent.mkdir(parents=True)
    real.write_text("")
    monkeypatch.setattr(detect, "managed_runtime_root", lambda: None)
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "OpenProgram Dev.app" / "Contents" / "MacOS" / "OpenProgram Dev"))
    assert catalog.base_interpreter() == str(real)

    real.unlink()
    assert catalog.base_interpreter() == sys.executable
