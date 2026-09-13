"""Creation publishes durable initial metadata before admitting later updates."""
from contextlib import ExitStack, closing, contextmanager

import pytest

from openprogram.store import SessionStore
from openprogram.store.session.git_session import GitSession


@pytest.fixture
def stores(tmp_path):
    with ExitStack() as stack:
        def new():
            return stack.enter_context(closing(SessionStore(tmp_path / 'sessions')))
        yield new(), new


@pytest.mark.parametrize('failure', [OSError, KeyboardInterrupt])
@pytest.mark.parametrize('after_write', [False, True])
def test_failed_creation_retry_uses_durable_admission(stores, monkeypatch, failure, after_write):
    store, new = stores
    original = GitSession.write_meta
    def fail(git, meta):
        if after_write:
            original(git, meta)
        raise failure('creation interrupted')
    with monkeypatch.context() as patch:
        patch.setattr(GitSession, 'write_meta', fail)
        with pytest.raises(failure):
            store.create_session('s', 'main', title='first')
    store.create_session('s', 'main', title='retry')
    for reader in (store, new()):
        assert reader.get_session('s')['title'] == ('first' if after_write else 'retry')


def test_creator_refreshes_admission_inside_lock(stores, monkeypatch):
    store, new = stores
    other = new()
    original = store._head_file_lock
    @contextmanager
    def interleave(git):
        other.create_session('s', 'first-agent', title='first durable')
        assert other.get_session('s')['title'] == 'first durable'
        with original(git):
            yield
    with monkeypatch.context() as patch:
        patch.setattr(store, '_head_file_lock', interleave)
        store.create_session('s', 'later-agent', title='later')
    for reader in (store, new()):
        assert reader.get_session('s')['title'] == 'first durable'
        assert reader.get_session('s')['agent_id'] == 'first-agent'


def test_creation_preserves_preexisting_history(stores):
    store, new = stores
    store.append_message('s', {'id': 'root', 'role': 'user', 'content': 'before'})
    store.update_session('s', pinned=True)
    store.create_session('s', 'main', title='created', created_at=10, updated_at=11)
    for reader in (store, new()):
        meta = reader.get_session('s')
        assert (meta['title'], meta['head_id'], meta['pinned']) == ('created', 'root', True)
        assert (meta['created_at'], meta['updated_at']) == (10, 11)
        assert [node.id for node in reader.get_nodes('s')] == ['root']


def test_creation_detaches_caller_metadata(stores):
    store, new = stores
    extra = {'paths': ('one', 'two'), 'nested': {'value': 1}}
    store.create_session('s', 'main', custom=extra)
    extra['nested']['value'] = 2
    for reader in (store, new()):
        assert reader.get_session('s')['custom'] == {'paths': ['one', 'two'], 'nested': {'value': 1}}


def test_update_waits_for_initial_metadata_publication(stores, monkeypatch):
    from threading import Event, Thread

    store, new = stores
    other = new()
    entered_lock = Event()
    errors = []
    threads = []
    original_lock = other._head_file_lock
    original_write = GitSession.write_meta
    @contextmanager
    def observe_lock(git):
        entered_lock.set()
        with original_lock(git):
            yield
    def update():
        try:
            other.update_session('s', pinned=True)
        except BaseException as exc:
            errors.append(exc)
    def interleave(git, meta):
        if meta.get('id') == 's' and not threads:
            thread = Thread(target=update)
            threads.append(thread)
            thread.start()
            assert entered_lock.wait(5), 'metadata updater did not reach writer lock'
        original_write(git, meta)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(other, '_head_file_lock', observe_lock)
            patch.setattr(GitSession, 'write_meta', interleave)
            store.create_session('s', 'main', title='initial', source='cli')
            for thread in threads:
                thread.join(5)
                assert not thread.is_alive()
    finally:
        for thread in threads:
            thread.join(5)
    assert not errors
    for reader in (store, new()):
        meta = reader.get_session('s')
        assert (meta['title'], meta['source'], meta['pinned']) == ('initial', 'cli', True)
