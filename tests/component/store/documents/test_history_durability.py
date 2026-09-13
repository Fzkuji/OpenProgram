"""Failure and reopen contracts at the shared file-history boundary."""
import asyncio
import json
from pathlib import Path

import pytest

from openprogram import sandbox
from openprogram.programs.tools.files.read import read
from openprogram.programs.tools.files.write import write
from openprogram.store import SessionNodeWriter, SessionStore, _current_turn_id, _store
from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.store.snapshot.checkpoint import manifest
from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir, turn_manifest_path


@pytest.fixture
def history(tmp_path, monkeypatch):
    policy = sandbox.policy_snapshot()
    sandbox.install_policy_snapshot({'enabled': False, 'policy': None})
    store = SessionStore(root_path=tmp_path / 'sessions')
    sid, tid = 'durability', 'a1'
    store.create_session(sid, agent_id='main', title='history')
    store.append_message(sid, {'id': tid, 'role': 'assistant', 'content': 'edit', 'timestamp': 1.0})
    monkeypatch.setattr('openprogram.store.default_store', lambda: store)
    monkeypatch.setattr('openprogram.store.session.session_store.default_store', lambda: store)
    st = _store.set(SessionNodeWriter(store, sid))
    tt = _current_turn_id.set(tid)
    target = tmp_path / 'document.docx'
    target.write_bytes(b'original\0')
    asyncio.run(read.execute('read', {'file_path': str(target)}, None, None))
    try:
        yield store, sid, tid, target
    finally:
        from openprogram.store.snapshot.read_tracking import forget_session
        forget_session(sid)
        _current_turn_id.reset(tt)
        _store.reset(st)
        store.close()
        sandbox.install_policy_snapshot(policy)


def publish(target, payload):
    source = target.with_name('staged.docx')
    source.write_bytes(payload)
    result = asyncio.run(write.execute('publish', {'file_path': str(target), 'source_path': str(source)}, None, None))
    return result.content[0].text


def test_failed_followup_manifest_commit_preserves_published_version(history, monkeypatch):
    store, sid, tid, target = history
    assert 'Wrote' in publish(target, b'first\0')
    journal = CheckpointStore(store._session_dir(sid))
    first = journal.list_mutations(tid)[0]
    blob = turn_backup_dir(journal.session_dir, tid) / first['after']['blob_ref']
    def fail_commit(*args, **kwargs):
        raise OSError('manifest commit interrupted')
    monkeypatch.setattr(manifest, 'commit', fail_commit)
    assert 'commit failed' in publish(target, b'second\0')
    assert blob.read_bytes() == b'first\0'
    assert journal.list_file_history(tid)[0]['recoverability'] == 'unavailable'
    assert journal.plan_history_operation(tid, 'revert')['status'] == 'unavailable'
    journal.abort_edit(tid, str(target), 'later retry also failed')
    assert journal.list_file_history(tid)[0]['recoverability'] == 'unavailable'


def test_aborted_attempt_retry_uses_fresh_before_version(history):
    store, sid, tid, target = history
    journal = CheckpointStore(store._session_dir(sid))
    journal.backup_before_edit(tid, str(target))
    journal.abort_edit(tid, str(target), 'no mutation applied')
    target.write_bytes(b'external revision\0')
    asyncio.run(read.execute('read-current', {'file_path': str(target)}, None, None))
    assert 'Wrote' in publish(target, b'after retry\0')
    result = journal.apply_history_operation(tid, 'revert', idempotency_key='retry-undo')
    assert result['status'] == 'committed', result
    assert target.read_bytes() == b'external revision\0'


@pytest.mark.parametrize('raw', ['{broken', '{"version": 99, "files": {}}', '{"files": {"bad": 2}}', '{"version": 2, "files": {"bad": {}}}'])
def test_corrupt_manifest_blocks_public_write_without_erasing_history(history, raw):
    store, sid, tid, target = history
    path = turn_manifest_path(store._session_dir(sid), tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw)
    result = publish(target, b'new\0')
    assert 'preparation failed' in result
    assert target.read_bytes() == b'original\0'
    assert path.read_text() == raw
    class WS:
        sent = []
        async def send_text(self, text):
            self.sent.append(json.loads(text))
    from openprogram.webui.ws_actions.turn_files import handle_review_scope
    ws = WS()
    asyncio.run(handle_review_scope(ws, {'session_id': sid, 'assistant_msg_id': tid, 'scope': 'turn'}))
    assert ws.sent[0]['data']['status'] == 'error'
    assert path.read_text() == raw


def test_interrupted_write_is_visible_after_reopen(history, monkeypatch):
    store, sid, tid, target = history
    def fail_commit(*args, **kwargs):
        raise OSError('interrupted before receipt commit')
    monkeypatch.setattr(manifest, 'commit', fail_commit)
    assert 'commit failed' in publish(target, b'changed\0')
    store.close()
    reopened = SessionStore(root_path=store.root_path)
    monkeypatch.setattr('openprogram.store.default_store', lambda: reopened)
    monkeypatch.setattr('openprogram.store.session.session_store.default_store', lambda: reopened)
    class WS:
        sent = []
        async def send_text(self, text):
            self.sent.append(json.loads(text))
    from openprogram.webui.ws_actions.turn_files import handle_review_scope
    ws = WS()
    try:
        asyncio.run(handle_review_scope(ws, {'session_id': sid, 'assistant_msg_id': tid, 'scope': 'turn'}))
        result = ws.sent[0]['data']
        assert result['files'], result
        assert result['files'][0]['recoverability'] == 'unavailable'
        assert result['files'][0]['unavailable_reason'] == 'mutation_incomplete'
        assert target.read_bytes() == b'changed\0'
    finally:
        reopened.close()


def test_snapshot_sync_failure_prevents_public_target_write(history, monkeypatch):
    import os
    store, sid, tid, target = history
    original = os.fsync
    calls = []
    def fail_sync(fd):
        calls.append(fd)
        raise OSError('disk sync failed')
    monkeypatch.setattr(os, 'fsync', fail_sync)
    result = publish(target, b'new content\0')
    monkeypatch.setattr(os, 'fsync', original)
    assert calls
    assert 'preparation failed' in result
    assert target.read_bytes() == b'original\0'
    assert not CheckpointStore(store._session_dir(sid)).list_mutations(tid)


def test_failed_writer_with_partial_side_effect_is_not_hidden(history):
    store, sid, tid, target = history
    journal = CheckpointStore(store._session_dir(sid))
    journal.backup_before_edit(tid, str(target))
    target.write_bytes(b'partial write\0')
    journal.abort_edit(tid, str(target), 'writer failed after touching target')
    assert journal.list_mutations(tid) == []
    assert journal.list_file_history(tid)[0]['unavailable_reason'] == 'mutation_incomplete'


def test_legacy_before_only_snapshot_remains_readable(history):
    store, sid, tid, target = history
    path = turn_manifest_path(store._session_dir(sid), tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / 'legacy').write_bytes(b'legacy before\0')
    path.write_text(json.dumps({'version': 1, 'files': {'legacy': {
        'path': str(target), 'pre_existing': True,
    }}}))
    assert CheckpointStore(store._session_dir(sid)).restore_turn(tid) == [str(target)]
    assert target.read_bytes() == b'legacy before\0'
