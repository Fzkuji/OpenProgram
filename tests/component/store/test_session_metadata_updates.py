"""Session metadata transformations preserve durable state across writers."""
from contextlib import contextmanager

import pytest

from openprogram.store import SessionStore


@pytest.fixture
def stores(tmp_path):
    opened = []
    def new():
        store = SessionStore(tmp_path / 'sessions')
        opened.append(store)
        return store
    first, second = new(), new()
    first.create_session('meta', 'main', title='original')
    first.append_message('meta', {'id': 'root', 'role': 'user', 'content': 'root'})
    second._open('meta')
    try:
        yield first, second, new
    finally:
        for store in reversed(opened):
            store.close()


@pytest.mark.parametrize('operation', ['session', 'branch', 'counter'])
def test_intervening_committed_metadata_survives(stores, monkeypatch, operation):
    first, second, new = stores
    original = first._head_file_lock
    @contextmanager
    def interleave(git):
        if operation == 'session':
            second.update_session('meta', pinned=True)
        elif operation == 'branch':
            second.set_branch_meta('meta', 'root', archived=True)
        else:
            second.bump_branch_turns('meta', 'root')
        with original(git):
            yield
    monkeypatch.setattr(first, '_head_file_lock', interleave)
    if operation == 'session':
        first.update_session('meta', title='renamed')
        assert new().get_session('meta')['pinned'] is True
    elif operation == 'branch':
        first.set_branch_name('meta', 'root', 'renamed')
        branch = new().get_branch_meta('meta', 'root')
        assert branch['archived'] is True
        assert branch['name'] == 'renamed'
    else:
        assert first.bump_branch_turns('meta', 'root') == 2
        assert new().get_branch_meta('meta', 'root')['turns'] == 2


@pytest.mark.parametrize('operation', ['session', 'dictionary'])
def test_failed_metadata_write_does_not_publish_cache(stores, monkeypatch, operation):
    first, _, new = stores
    git, _ = first._open('meta')
    before = first.get_session('meta')
    def fail(*_args, **_kwargs):
        raise OSError('metadata unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(git, 'write_meta', fail)
        with pytest.raises(OSError, match='metadata unavailable'):
            if operation == 'session':
                first.update_session('meta', title='not committed')
            else:
                first.update_session_dict('meta', 'custom', lambda _: {'version': 1})
    for store in (first, new()):
        assert store.get_session('meta') == before
        assert 'custom' not in store._open('meta')[1].meta


def test_rejected_dictionary_callback_leaves_committed_state(stores):
    first, _, new = stores
    first.update_session_dict('meta', 'custom', lambda _: {'version': 1})
    def reject(current):
        current['version'] = 2
        return None
    assert first.update_session_dict('meta', 'custom', reject) is None
    for store in (first, new()):
        assert store._open('meta')[1].meta['custom']['version'] == 1


def test_metadata_write_keeps_intervening_history_visible(stores, monkeypatch):
    first, second, new = stores
    original = first._head_file_lock
    @contextmanager
    def interleave(git):
        second.append_message('meta', {
            'id': 'second', 'role': 'assistant', 'content': 'second', 'predecessor': 'root',
        })
        with original(git):
            yield
    with monkeypatch.context() as patch:
        patch.setattr(first, '_head_file_lock', interleave)
        first.update_session('meta', title='renamed')
    for store in (first, new()):
        assert {node.id for node in store.get_nodes('meta')} == {'root', 'second'}
        assert store.get_session('meta')['head_id'] == 'second'
        assert store.get_session('meta')['title'] == 'renamed'


def test_update_during_first_creation_preserves_initial_metadata(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / 'sessions')
    fresh = None
    original = store._persist_meta
    def interleave(git, index):
        store.update_session('creating', pinned=True)
        original(git, index)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, '_persist_meta', interleave)
            store.create_session('creating', 'main', title='Original title', source='cli', channel='test-channel')
        fresh = SessionStore(tmp_path / 'sessions')
        for reader in (store, fresh):
            current = reader.get_session('creating')
            assert current['title'] == 'Original title'
            assert current['source'] == 'cli'
            assert current['channel'] == 'test-channel'
            assert current['pinned'] is True
    finally:
        if fresh is not None:
            fresh.close()
        store.close()


@pytest.mark.parametrize('outcome', ['reject', 'write_failure'])
def test_pending_creation_callback_cannot_change_uncommitted_cache(tmp_path, monkeypatch, outcome):
    store = SessionStore(tmp_path / 'sessions')
    fresh = None
    original = store._persist_meta
    def interleave(git, index):
        def change(current):
            current['nested']['version'] = 2
            return None if outcome == 'reject' else current
        if outcome == 'reject':
            assert store.update_session_dict('creating', 'custom', change) is None
        else:
            def fail(*_args, **_kwargs):
                raise OSError('metadata unavailable')
            with monkeypatch.context() as patch:
                patch.setattr(git, 'write_meta', fail)
                with pytest.raises(OSError, match='metadata unavailable'):
                    store.update_session_dict('creating', 'custom', change)
        original(git, index)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, '_persist_meta', interleave)
            store.create_session('creating', 'main', custom={'nested': {'version': 1}})
        fresh = SessionStore(tmp_path / 'sessions')
        for reader in (store, fresh):
            assert reader._open('creating')[1].meta['custom'] == {'nested': {'version': 1}}
    finally:
        if fresh is not None:
            fresh.close()
        store.close()


def test_dictionary_update_result_does_not_mutate_cache(stores):
    first, _, new = stores
    result = first.update_session_dict('meta', 'custom', lambda _: {'version': 1})
    result['version'] = 2
    for store in (first, new()):
        assert store._open('meta')[1].meta['custom']['version'] == 1
