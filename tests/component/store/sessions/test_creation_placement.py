"""One session ID retains one placement across creators and retries."""
from contextlib import ExitStack, closing

import pytest

from openprogram.store import SessionStore
from openprogram.store.project import project_store as projects
from openprogram.store.session.git_session import GitSession


@pytest.fixture
def setup(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: state)
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    with ExitStack() as stack:
        def new():
            return stack.enter_context(closing(SessionStore(state / 'sessions')))
        yield new(), new, first, second


@pytest.mark.parametrize('fresh_creator', [False, True])
def test_existing_id_ignores_replacement_project(setup, fresh_creator):
    store, new, first, second = setup
    store.create_session('s', 'main', title='original', project_path=str(first))
    original = store._session_dir('s')
    creator = new() if fresh_creator else store
    creator.create_session('s', 'replacement', title='replacement', project_path=str(second))
    for reader in (store, creator, new()):
        assert reader.get_session('s')['title'] == 'original'
        assert reader._session_dir('s') == original
    assert projects.project_for_session('s').path == str(first.resolve())
    assert len(list(store.root_path.rglob('meta.json'))) == 1
    assert not (second / '.openprogram').exists()


def test_deleted_id_does_not_register_new_project(setup):
    store, _, first, second = setup
    store.create_session('s', 'main', project_path=str(first))
    store.delete_session('s')
    before = (store.root_path / 'locations.json').read_bytes()
    store.create_session('s', 'main', project_path=str(second))
    assert store.get_session('s') is None
    assert (store.root_path / 'locations.json').read_bytes() == before
    assert not (second / '.openprogram').exists()
    assert projects.project_for_session('s') is None


def test_retry_restores_indexes_from_committed_metadata(setup, monkeypatch):
    store, new, first, second = setup
    original = GitSession.write_meta
    def interrupt(git, meta):
        original(git, meta)
        raise KeyboardInterrupt('after metadata')
    with monkeypatch.context() as patch:
        patch.setattr(GitSession, 'write_meta', interrupt)
        with pytest.raises(KeyboardInterrupt):
            store.create_session('s', 'main', title='original', project_path=str(first))
    store.create_session('s', 'wrong', title='wrong', project_path=str(second))
    for reader in (store, new()):
        assert reader.get_session('s')['title'] == 'original'
        assert [(r['id'], r['title']) for r in reader.list_sessions()] == [('s', 'original')]
    assert projects.project_for_session('s').path == str(first.resolve())
    assert len(list(store.root_path.rglob('meta.json'))) == 1


def test_concurrent_project_creators_keep_first_placement(setup, monkeypatch):
    from contextlib import contextmanager
    from threading import Event, Thread, current_thread
    from openprogram.store.session.session_store import shared

    store, new, first, second = setup
    other = new()
    resolving = Event()
    release = Event()
    competing = Event()
    errors = []
    resolve = projects.resolve_project
    lock = shared.session_interprocess_lock
    def pause_resolution(path):
        if str(path) == str(first):
            resolving.set()
            assert release.wait(5), 'first creator was not released'
        return resolve(path)
    @contextmanager
    def observe_lock(*args, **kwargs):
        if current_thread().name == 'competing-creator':
            competing.set()
        with lock(*args, **kwargs):
            yield
    def create(target, path, title):
        try:
            target.create_session('s', 'main', title=title, project_path=str(path))
        except BaseException as exc:
            errors.append(exc)
    threads = [Thread(target=create, args=(store, first, 'first')),
               Thread(target=create, args=(other, second, 'second'), name='competing-creator')]
    try:
        with monkeypatch.context() as patch:
            patch.setattr(projects, 'resolve_project', pause_resolution)
            patch.setattr(shared, 'session_interprocess_lock', observe_lock)
            threads[0].start()
            assert resolving.wait(5)
            threads[1].start()
            assert competing.wait(5)
            release.set()
            for thread in threads:
                thread.join(5)
                assert not thread.is_alive()
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(5)
    assert not errors
    assert len(list(store.root_path.rglob('meta.json'))) == 1
    for reader in (store, other, new()):
        assert reader.get_session('s')['title'] == 'first'
        assert reader.list_sessions()[0]['title'] == 'first'
    assert projects.project_for_session('s').path == str(first.resolve())
