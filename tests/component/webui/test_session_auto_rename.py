"""Explicit session rename through the public WebSocket action."""
import asyncio
import json

import pytest
from openprogram.runtime.context.session_db import SessionDB
from openprogram.interfaces.server.ws_actions import session as actions
from openprogram.interfaces.server import server


class Socket:
    def __init__(self):
        self.frames = []

    async def send_text(self, text):
        self.frames.append(json.loads(text))


@pytest.fixture
def state(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / 'sessions.sqlite')
    db.create_session('rename-test', 'main', title='Old title')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: db)
    monkeypatch.setattr(server, '_sessions', {'rename-test': {'title': 'Old title'}})
    broadcasts = []
    monkeypatch.setattr(server, '_broadcast', lambda text: broadcasts.append(json.loads(text)))
    return db, Socket(), broadcasts


def run(ws, **kwargs):
    async def finish():
        task = await actions.handle_rename_session(ws, {'session_id': 'rename-test', 'request_id': 'r1', **kwargs})
        if task is not None:
            await task
    asyncio.run(finish())


def test_auto_rename_persists_broadcasts_and_replies(state, monkeypatch):
    db, ws, frames = state
    monkeypatch.setattr(actions.lifecycle, '_llm_rename', lambda sid: 'Generated title')
    run(ws)
    assert db.get_session('rename-test')['title'] == 'Generated title'
    assert server._sessions['rename-test']['title'] == 'Generated title'
    assert ws.frames[-1]['data']['request_id'] == 'r1'
    assert ws.frames[-1]['data']['status'] == 'ok'
    assert frames[-1]['data']['title'] == 'Generated title'


def test_failed_generation_preserves_title_with_feedback(state, monkeypatch):
    db, ws, frames = state
    monkeypatch.setattr(actions.lifecycle, '_llm_rename', lambda sid: None)
    run(ws)
    assert db.get_session('rename-test')['title'] == 'Old title'
    assert not frames
    assert ws.frames[-1]['data']['status'] == 'failed'


@pytest.mark.parametrize('mutation', ['rename', 'delete'])
def test_generation_cannot_replace_newer_name_or_recreate_deleted_session(state, monkeypatch, mutation):
    db, ws, frames = state
    def generate(sid):
        if mutation == 'delete':
            db.delete_session(sid)
        else:
            db.update_session(sid, title='Manual title', _user_titled=True)
        return 'Stale generation'
    monkeypatch.setattr(actions.lifecycle, '_llm_rename', generate)
    run(ws)
    current = db.get_session('rename-test')
    assert current is None if mutation == 'delete' else current['title'] == 'Manual title'
    assert not frames
    assert ws.frames[-1]['data']['status'] == 'superseded'


def test_function_only_session_can_generate_title(state, monkeypatch):
    db, ws, frames = state
    monkeypatch.setattr(db, 'get_branch', lambda sid: [{'role': 'code', 'content': 'Weekly report completed'}])
    inputs = []
    monkeypatch.setattr('openprogram.runtime.conversation.turn_runtime.titles._generate_llm_title', lambda u, a: inputs.append((u,a)) or 'Weekly report')
    run(ws)
    assert inputs == [('Old title', 'Weekly report completed')]
    assert db.get_session('rename-test')['title'] == 'Weekly report'


def test_manual_rename_never_calls_model(state, monkeypatch):
    db, ws, frames = state
    monkeypatch.setattr(actions.lifecycle, '_llm_rename', lambda sid: pytest.fail('manual rename called model'))
    run(ws, title='Chosen name')
    assert db.get_session('rename-test')['extra_meta']['_user_titled'] is True
    assert db.get_session('rename-test')['title'] == 'Chosen name'


def test_model_generation_does_not_block_event_loop(state, monkeypatch):
    import threading
    _, ws, _ = state
    released = threading.Event()
    def generate(sid):
        assert released.wait(30), 'model generation blocked the event loop'
        return 'Generated title'
    monkeypatch.setattr(actions.lifecycle, '_llm_rename', generate)
    async def scenario():
        task = await actions.handle_rename_session(ws, {'session_id': 'rename-test'})
        await asyncio.sleep(0.05)
        released.set()
        await task
    asyncio.run(scenario())
    assert ws.frames[-1]['data']['status'] == 'ok'


def test_failed_persistence_does_not_broadcast_success(state, monkeypatch):
    db, ws, frames = state
    def fail(*args, **kwargs):
        raise OSError('disk failure')
    monkeypatch.setattr(db, 'update_session', fail)
    run(ws, title='New title')
    assert not frames
    assert ws.frames[-1]['data']['status'] == 'failed'
    assert db.get_session('rename-test')['title'] == 'Old title'


def test_same_connection_manual_rename_during_generation(state, monkeypatch):
    import threading
    db, ws, frames = state
    started, released = threading.Event(), threading.Event()
    def generate(sid):
        started.set()
        assert released.wait(30)
        return 'Stale generated title'
    monkeypatch.setattr(actions.lifecycle, '_llm_rename', generate)
    async def scenario():
        task = await actions.handle_rename_session(ws, {'session_id': 'rename-test', 'request_id': 'auto'})
        assert isinstance(task, asyncio.Task)
        try:
            assert await asyncio.to_thread(started.wait, 2)
            await actions.handle_rename_session(ws, {'session_id': 'rename-test', 'title': 'Manual name', 'request_id': 'manual'})
            assert db.get_session('rename-test')['title'] == 'Manual name'
        finally:
            released.set()
            await task
        await asyncio.sleep(0)
        assert task not in actions._rename_tasks
    asyncio.run(scenario())
    assert [f['data']['status'] for f in ws.frames] == ['ok', 'superseded']
    assert len(frames) == 1
