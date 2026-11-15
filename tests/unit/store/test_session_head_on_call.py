"""A stored HEAD on a call row resolves to the reply that made it."""
from openprogram.state.store import SessionStore


def test_stored_head_on_a_call_reopens_on_its_reply(tmp_path):
    sid = 'head-on-call'
    store = SessionStore(tmp_path / 'sessions')
    store.create_session(sid, 'main')
    store.append_message(sid, {'id': 'u1', 'role': 'user', 'content': 'hi', 'predecessor': None})
    store.append_message(sid, {'id': 'a1', 'role': 'assistant', 'content': 'on it', 'predecessor': 'u1'})
    store.append_message(sid, {'id': 'a1_t_call_1', 'role': 'tool', 'function': 'list',
                               'content': 'ok', 'caller': 'a1', 'predecessor': ''})
    # What older builds could leave behind: HEAD on the call itself.
    store.update_session(sid, head_id='a1_t_call_1')

    reopened = SessionStore(tmp_path / 'sessions')
    assert reopened.get_session(sid)['head_id'] == 'a1'
    assert [m['id'] for m in reopened.get_branch(sid)] == ['u1', 'a1']
