"""Public append entries preserve durable state across interleaving and failures."""
import pytest

from openprogram.context.nodes import Call
from openprogram.store.session.session_node_writer import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


@pytest.fixture
def stores(tmp_path):
    opened = []
    def new():
        store = SessionStore(tmp_path / 'sessions')
        opened.append(store)
        return store
    first, second = new(), new()
    first.create_session('append', 'main')
    first.append_message('append', {'id': 'root', 'role': 'user', 'content': 'root'})
    second._open('append')
    try:
        yield first, second, new
    finally:
        for store in reversed(opened):
            store.close()


def append(store, method, node_id):
    if method == 'message':
        store.append_message('append', {'id': node_id, 'role': 'assistant', 'content': node_id, 'predecessor': 'root'})
    else:
        SessionNodeWriter(store, 'append').append(Call(id=node_id, role='assistant', output=node_id, predecessor='root'))


@pytest.mark.parametrize('method', ['message', 'writer'])
def test_two_committed_appends_have_distinct_sequences(stores, monkeypatch, method):
    first, second, new = stores
    original = first._open
    def interleave(*args, **kwargs):
        pair = original(*args, **kwargs)
        append(second, 'message', 'second')
        return pair
    monkeypatch.setattr(first, '_open', interleave)
    append(first, method, 'first')
    nodes = new().get_nodes('append')
    assert {n.id for n in nodes} == {'root', 'first', 'second'}
    assert len({n.seq for n in nodes}) == len(nodes)


@pytest.mark.parametrize('method', ['message', 'writer'])
def test_history_failure_does_not_publish_a_phantom_node(stores, monkeypatch, method):
    first, _, new = stores
    git, _ = first._open('append')
    def fail(*_args, **_kwargs):
        raise OSError('history unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(git, 'write_history', fail)
        with pytest.raises(OSError, match='history unavailable'):
            append(first, method, 'first')
    assert [n.id for n in first.get_nodes('append')] == ['root']
    append(first, method, 'first')
    assert {n.id for n in new().get_nodes('append')} == {'root', 'first'}


@pytest.mark.parametrize('method', ['message', 'writer'])
def test_retry_after_metadata_failure_completes_durable_head(stores, monkeypatch, method):
    first, _, new = stores
    git, _ = first._open('append')
    def fail(*_args, **_kwargs):
        raise OSError('metadata unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(git, 'write_meta', fail)
        with pytest.raises(OSError, match='metadata unavailable'):
            append(first, method, 'first')
    append(first, method, 'first')
    fresh = new()
    assert {n.id for n in fresh.get_nodes('append')} == {'root', 'first'}
    assert fresh.get_session('append')['head_id'] == 'first'


def test_replay_preserves_another_committed_patch(stores, monkeypatch):
    first, second, new = stores
    original = first._open
    def interleave(*args, **kwargs):
        pair = original(*args, **kwargs)
        second.merge_node_metadata('append', 'root', {'committed': True})
        return pair
    monkeypatch.setattr(first, '_open', interleave)
    SessionNodeWriter(first, 'append').append(Call(id='root', role='user', output='root'))
    assert new().get_nodes('append')[0].metadata['committed'] is True


@pytest.mark.parametrize('method', ['message', 'writer'])
def test_reopen_recovers_metadata_before_later_head_change(stores, monkeypatch, method):
    first, _, new = stores
    git, _ = first._open('append')
    def fail(*_args, **_kwargs):
        raise OSError('metadata unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(git, 'write_meta', fail)
        with pytest.raises(OSError, match='metadata unavailable'):
            append(first, method, 'first')
    fresh = new()
    assert fresh.get_session('append')['head_id'] == 'first'
    assert {n.id for n in fresh.get_nodes('append')} == {'root', 'first'}
    fresh.set_head('append', 'root')
    append(first, method, 'first')
    assert new().get_session('append')['head_id'] == 'root'


@pytest.mark.parametrize('advance_head,caller', [(False, ''), (True, 'root')])
def test_recovery_retains_node_writer_head_ownership(stores, monkeypatch, advance_head, caller):
    first, _, new = stores
    git, _ = first._open('append')
    def fail(*_args, **_kwargs):
        raise OSError('metadata unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(git, 'write_meta', fail)
        with pytest.raises(OSError, match='metadata unavailable'):
            SessionNodeWriter(first, 'append', advance_head=advance_head).append(Call(
                id='first', role='llm', output='first', predecessor='root', caller=caller,
            ))
    fresh = new()
    assert fresh.get_session('append')['head_id'] == 'root'
    assert {n.id for n in fresh.get_nodes('append')} == {'root', 'first'}


def test_explicit_sequence_is_preserved_and_collision_rejected(stores):
    first, _, new = stores
    writer = SessionNodeWriter(first, 'append')
    writer.append(Call(id='first', role='llm', predecessor='root', seq=7))
    with pytest.raises(ValueError, match='sequence already exists'):
        writer.append(Call(id='collision', role='llm', predecessor='first', seq=7))
    writer.append(Call(id='next', role='llm', predecessor='first'))
    assert [(n.id, n.seq) for n in new().get_nodes('append')] == [('root', 0), ('first', 7), ('next', 8)]


@pytest.mark.parametrize('method', ['message', 'writer'])
def test_later_metadata_patch_follows_recovered_append(stores, monkeypatch, method):
    first, second, new = stores
    git, _ = first._open('append')
    def fail(*_args, **_kwargs):
        raise OSError('metadata unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(git, 'write_meta', fail)
        with pytest.raises(OSError, match='metadata unavailable'):
            append(first, method, 'first')
    second.update_session('append', title='later title')
    fresh = new()
    assert fresh.get_session('append')['title'] == 'later title'
    assert fresh.get_session('append')['head_id'] == 'first'


def test_pending_recovery_revalidates_relocated_cached_session(stores, monkeypatch):
    from contextlib import contextmanager

    first, second, new = stores
    git, _ = first._open('append')
    def fail(*_args, **_kwargs):
        raise OSError('metadata unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(git, 'write_meta', fail)
        with pytest.raises(OSError, match='metadata unavailable'):
            append(first, 'writer', 'first')
    source = git.path
    destination = first.root_path / 'projects' / 'moved' / 'append'
    original_lock = first._head_file_lock
    @contextmanager
    def relocate(current):
        if source.exists():
            destination.parent.mkdir(parents=True)
            source.rename(destination)
            second._record_location('append', destination)
        with original_lock(current):
            yield
    monkeypatch.setattr(first, '_head_file_lock', relocate)
    assert first.get_session('append')['head_id'] == 'first'
    assert [n.id for n in first.get_nodes('append')] == ['root', 'first']
    assert new().get_session('append')['head_id'] == 'first'
    assert not source.exists()
