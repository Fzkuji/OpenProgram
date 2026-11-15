"""A session whose HEAD rests on a folded call row still loads its chat."""
import asyncio
import json

from openprogram.state.store import SessionStore
from openprogram.interfaces.server.ws_actions import session as ws_session


def test_head_on_a_call_row_loads_the_turn_it_belongs_to(tmp_path, monkeypatch):
    from openprogram.interfaces.server import server
    sid = 'head-on-call-chat'
    store = SessionStore(tmp_path / 'sessions')
    store.create_session(sid, 'main')
    store.append_message(sid, {'id': 'u1', 'role': 'user', 'content': 'hi', 'predecessor': None})
    store.append_message(sid, {'id': 'a1', 'role': 'assistant', 'content': 'on it', 'predecessor': 'u1'})
    # A call hangs off its reply by caller, with no predecessor — and a
    # run that stopped mid-turn can leave HEAD on it.
    store.append_message(sid, {'id': 'a1_t_call_1', 'role': 'tool', 'function': 'list',
                               'content': 'ok', 'caller': 'a1', 'predecessor': ''})
    store.update_session(sid, head_id='a1_t_call_1')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: store)
    monkeypatch.setattr(server, '_get_provider_info', lambda _sid=None: {})
    monkeypatch.setattr(server, '_is_run_active', lambda _sid: False)
    monkeypatch.setattr(server, '_canonical_foreground_task', lambda _sid: None)

    class WS:
        def __init__(self):
            self.frames = []

        async def send_text(self, text):
            self.frames.append(json.loads(text))

    ws = WS()
    with server._sessions_lock:
        server._sessions[sid] = {'id': sid}
    try:
        asyncio.run(ws_session.handle_load_session(ws, {'session_id': sid}))
        loaded = next(f['data'] for f in ws.frames if f['type'] == 'session_loaded')
        ids = [m['id'] for m in loaded['messages']]
        assert 'u1' in ids and 'a1' in ids
    finally:
        with server._sessions_lock:
            server._sessions.pop(sid, None)
