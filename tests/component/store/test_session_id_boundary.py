"""Session identifiers cannot select paths outside their owned namespace."""
from contextlib import closing

import pytest

from openprogram.store import SessionStore, SessionNodeWriter
from openprogram.context.nodes import Call
from openprogram.store.session import placement, migration


def snapshot(root):
    return sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None)
                  for p in root.rglob('*'))


@pytest.mark.parametrize('method', ['create', 'append', 'delete', 'writer'])
@pytest.mark.parametrize('identifier', ['../victim', 'absolute', '.locks', 'PROJECTS', '.git', '', '.', '..', 'a/b', 'a\\b', 'bad\x00id', None, []])
def test_invalid_mutation_preserves_fixture_tree(tmp_path, monkeypatch, method, identifier):
    monkeypatch.setattr('openprogram.store.project.project_store.unbind_session', lambda *_: None)
    victim = tmp_path / 'victim'
    victim.mkdir()
    (victim / 'sentinel').write_text('retained')
    with closing(SessionStore(tmp_path / 'sessions')) as store:
        if identifier == 'absolute':
            identifier = str(victim)
        before = snapshot(tmp_path)
        with pytest.raises(ValueError, match='session_id'):
            if method == 'create':
                store.create_session(identifier, 'main')
            elif method == 'append':
                store.append_message(identifier, {'id': 'node', 'role': 'user'})
            elif method == 'delete':
                store.delete_session(identifier)
            else:
                SessionNodeWriter(store, identifier).append(Call(id='node', role='user'), create_if_missing=False)
        assert snapshot(tmp_path) == before


@pytest.mark.parametrize('identifier', ['../victim', '/absolute', '.locks', 'PROJECTS', '.git', '', '.', '..', 'a\\b', 'bad\x00id', None, []])
def test_invalid_reads_remain_empty(tmp_path, identifier):
    with closing(SessionStore(tmp_path / 'sessions')) as store:
        before = snapshot(tmp_path)
        assert store.get_session(identifier) is None
        assert store.get_messages(identifier) == []
        from openprogram.agent.job.store import list_jobs, load_job
        assert list_jobs(identifier) == []
        assert load_job(identifier, "missing") is None
        assert snapshot(tmp_path) == before


@pytest.mark.parametrize('identifier', ['normal-id', 'session.name', '会话-一'])
def test_valid_session_round_trip(tmp_path, monkeypatch, identifier):
    monkeypatch.setattr('openprogram.store.project.project_store.unbind_session', lambda *_: None)
    with closing(SessionStore(tmp_path / 'sessions')) as store:
        store.create_session(identifier, 'main')
        store.append_message(identifier, {'id': 'node', 'role': 'user'})
        assert store.get_messages(identifier)[0]['session_id'] == identifier
        store.delete_session(identifier)
        assert store.get_session(identifier) is None


def test_raw_paths_and_migration_reject_before_side_effects(tmp_path):
    calls = [
        lambda: placement.default_session_dir(tmp_path, '../victim'),
        lambda: placement.nested_session_dir(tmp_path, 'project', '../victim'),
        lambda: placement.legacy_project_session_dir(tmp_path, '../victim'),
        lambda: placement.record_delete_intent(tmp_path, '../victim', {}),
        lambda: migration.staging_dir(tmp_path, '../victim'),
        lambda: migration.hold_path(tmp_path, '../victim'),
    ]
    with closing(SessionStore(tmp_path / 'sessions')) as store:
        calls.append(lambda: migration.migrate_session(store, {'session_id': '../victim'}))
        before = snapshot(tmp_path)
        for call in calls:
            with pytest.raises(ValueError, match='session_id'):
                call()
        assert snapshot(tmp_path) == before
