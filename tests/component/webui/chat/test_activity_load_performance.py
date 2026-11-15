"""Activity summaries do not hydrate event bodies or scan unrelated sessions."""
from types import SimpleNamespace


def test_snapshot_cursor_uses_index_without_parsing_event_bodies(tmp_path, monkeypatch):
    from openprogram.runtime.execution import ExecutionStore, CapabilitySet
    from openprogram.runtime.execution.public import _event_sequence
    store = ExecutionStore(tmp_path / 'executions.db')
    revision = store.create_revision(manifest={'entrypoint': 'workflow'})
    execution = store.admit_execution(
        execution_id='one', run_id='run', session_id='session',
        revision_id=revision.revision_id, input_ref='blob:input', input_hash='hash',
        entrypoint='workflow', trusted_actor={'subject': 'owner'},
        config_snapshot_ref='blob:config', user_message_id='user', assistant_message_id='assistant',
        capabilities=CapabilitySet(),
    )
    expected = max(event.execution_sequence for event in store.list_events('one'))
    monkeypatch.setattr(store, 'list_events', lambda *_: (_ for _ in ()).throw(AssertionError('event bodies read')))
    assert _event_sequence(store, execution.execution_id, 0) == expected
    assert _event_sequence(store, 'missing', 9) == 9


def test_canonical_job_lookup_reads_only_its_home(monkeypatch):
    import threading
    from openprogram.runtime.jobs.runner.progress import ProgressOperations
    from openprogram.runtime.jobs.runner import shared
    reads = []
    monkeypatch.setattr(shared, '_store_load', lambda sid, jid: reads.append((sid, jid)))
    runner = SimpleNamespace(_lock=threading.Lock(), _jobs={},
        _execution_store=SimpleNamespace(get_execution=lambda _: SimpleNamespace(session_id='home')))
    assert ProgressOperations._find_session_for_job(runner, 'foreground') is None
    assert reads == [('home', 'foreground')]
    monkeypatch.setattr(shared, '_store_load', lambda sid, jid: SimpleNamespace(id=jid))
    assert ProgressOperations._find_session_for_job(runner, 'job') == 'home'
