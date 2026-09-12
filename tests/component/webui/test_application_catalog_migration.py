"""Public app management preserves legacy registrations and separates abilities."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_legacy_application_moves_out_of_program_sources(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.webui.routes import applications
    from openprogram.programs import _programs
    from openprogram.programs._applications import catalog
    source = tmp_path / 'app'
    source.mkdir()
    (source / 'index.html').write_text('<!doctype html><title>Notes</title>')
    (source / 'application.json').write_text(json.dumps({
        'id': 'test.migrate', 'title': 'Notes', 'version': '1', 'capabilities': ['storage.app'],
    }))
    app = FastAPI()
    applications.register(app)
    with TestClient(app) as client:
        definition = client.post('/api/applications/install', json={'path': str(source)}).json()
        opened = client.post('/api/applications/test.migrate/open', json={}).json()
        state_url = '/api/application-instances/' + opened['instance_id'] + '/state'
        assert client.put(state_url, json={'value': {'note': 'retained'}, 'version': 0}).status_code == 200
        # Recreate the released legacy layout, retaining real immutable content/data.
        new_file = catalog.home() / 'catalog.json'
        new_file.unlink(missing_ok=True)
        package = {'kind': 'git', 'path': str(tmp_path / 'legacy/packages/gui_harness'), 'source': 'fixture'}
        legacy = {'kind': 'application', 'path': str(catalog.home() / 'versions' / definition['digest']),
                  'source': str(source), 'application': {**definition, 'hidden': True}}
        _programs._write_program_sources([package, legacy])
        response = client.get('/api/applications')
        assert response.status_code == 200
        assert response.json()['applications'][0]['hidden'] is True
        assert new_file.is_file()
        assert _programs._read_program_sources() == [package]
        assert client.get(state_url).json()['value'] == {'note': 'retained'}
        assert client.get('/api/applications').json() == response.json()
        assert client.post('/api/applications/test.migrate/open', json={}).json()['instance_id'] == opened['instance_id']


def test_migration_retries_after_legacy_cleanup_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.programs import _programs
    from openprogram.programs._applications import catalog_store
    import pytest
    row = {'kind': 'application', 'uninstalled': True, 'application': {'id': 'test.deleted', 'enabled': False}}
    _programs._write_program_sources([row])
    original = _programs._write_program_sources
    with monkeypatch.context() as patch:
        patch.setattr(_programs, '_write_program_sources', lambda rows: (_ for _ in ()).throw(OSError('disk failure')))
        with pytest.raises(OSError, match='disk failure'):
            catalog_store.read()
    assert json.loads(catalog_store.path().read_text())['applications'] == [row]
    assert _programs._read_program_sources() == [row]
    assert catalog_store.read() == [row]
    assert _programs._read_program_sources() == []
    original([])


def test_migration_conflict_preserves_both_catalogs(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.programs import _programs
    from openprogram.programs._applications import catalog_store
    import pytest
    legacy = {'kind': 'application', 'application': {'id': 'test.conflict', 'enabled': True}}
    current = {'kind': 'application', 'application': {'id': 'test.conflict', 'enabled': False}}
    _programs._write_program_sources([legacy])
    catalog_store.write([current])
    with pytest.raises(ValueError, match='migration conflict'):
        catalog_store.read()
    assert _programs._read_program_sources() == [legacy]
    assert json.loads(catalog_store.path().read_text())['applications'] == [current]


def test_new_catalog_write_failure_preserves_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    from openprogram.programs import _programs
    from openprogram.programs._applications import catalog_store
    import pytest
    row = {'kind': 'application', 'application': {'id': 'test.retained'}}
    _programs._write_program_sources([row])
    monkeypatch.setattr(catalog_store, 'write', lambda rows: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        catalog_store.read()
    assert _programs._read_program_sources() == [row]
    assert not catalog_store.path().exists()
