from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore
from openprogram.context.nodes import Call


def test_activity_projects_conversation_branches_not_turns(tmp_path):
    from openprogram.execution.activity_branches import conversation_activity_branches

    db = SessionStore(tmp_path / 'sessions')
    writer = SessionNodeWriter(db, 'conversation')
    for node in [Call(id='u1', role='user', predecessor='ROOT'),
                 Call(id='a1', role='llm', predecessor='u1'),
                 Call(id='u2', role='user', predecessor='a1'),
                 Call(id='a2', role='llm', predecessor='u2')]:
        writer.append(node)
    def item(eid, user, assistant):
        return {'execution_id': eid, 'session_id': 'conversation', 'snapshot': {
            'display': {'user_message_id': user, 'assistant_message_id': assistant}}}
    items = [item('e1', 'u1', 'a1'), item('e2', 'u2', 'a2')]
    branches = conversation_activity_branches(items, session_store=db)
    assert len(branches) == 1
    assert set(branches[0]['execution_ids']) == {'e1', 'e2'}
    writer.append(Call(id='a2retry', role='llm', predecessor='u2'))
    items.append(item('retry', 'u2', 'a2retry'))
    branches = conversation_activity_branches(items, session_store=db)
    assert {b['head_msg_id']: set(b['execution_ids']) for b in branches} == {
        'a2': {'e1', 'e2'}, 'a2retry': {'e1', 'retry'}}
    writer.append(Call(id='u3', role='user', predecessor='a2retry'))
    writer.append(Call(id='a3', role='llm', predecessor='u3'))
    items.append(item('e3', 'u3', 'a3'))
    branches = conversation_activity_branches(items, session_store=db)
    assert {b['head_msg_id']: set(b['execution_ids']) for b in branches} == {
        'a2': {'e1', 'e2'}, 'a3': {'e1', 'retry', 'e3'}}


def test_called_session_does_not_expose_unrelated_branch_tips(tmp_path):
    from openprogram.execution.activity_branches import conversation_activity_branches
    db = SessionStore(tmp_path / 'sessions')
    writer = SessionNodeWriter(db, 'target')
    for node in [Call(id='user', role='user', predecessor='ROOT'),
                 Call(id='allowed', role='llm', predecessor='user'),
                 Call(id='private-user', role='user', predecessor='allowed'),
                 Call(id='private-tip', role='llm', predecessor='private-user')]:
        writer.append(node)
    db.set_branch_name('target', 'private-tip', 'private unrelated branch title')
    items = [{'execution_id': 'allowed-execution', 'session_id': 'target', 'snapshot': {
        'display': {'user_message_id': 'user', 'assistant_message_id': 'allowed'}}}]
    branches = conversation_activity_branches(items, conversation_session_id='caller', session_store=db)
    assert branches == [{'branch_id': 'target:allowed', 'session_id': 'target',
                         'head_msg_id': 'allowed', 'name': None, 'execution_ids': ['allowed-execution']}]


def test_session_endpoint_returns_branch_membership_after_authorization(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from openprogram_server._webui.routes import lifecycle
    db = SessionStore(tmp_path / 'sessions')
    writer = SessionNodeWriter(db, 'session')
    writer.append(Call(id='user', role='user', predecessor='ROOT'))
    writer.append(Call(id='assistant', role='llm', predecessor='user'))
    monkeypatch.setattr('openprogram.agent.session_db.default_db', lambda: db)
    monkeypatch.setattr('openprogram.execution.default_store', lambda: SimpleNamespace(get_agent_turn_input=lambda _: {}))
    execution = SimpleNamespace(execution_id='run', session_id='session', status=SimpleNamespace(value='completed'), created_at=1, status_version=1)
    monkeypatch.setattr('openprogram.execution.conversation_scope.conversation_execution_scope', lambda *_: ([execution], {}))
    monkeypatch.setattr(lifecycle, '_execution_payload', lambda _: {'display': {'user_message_id': 'user', 'assistant_message_id': 'assistant'}})
    monkeypatch.setattr(lifecycle, '_actor_and_session', lambda _: ({}, 'session'))
    monkeypatch.setattr('openprogram.execution.authorization.authorize_session_action', lambda *_: None)
    app = FastAPI()
    lifecycle.register(app)
    with TestClient(app) as client:
        response = client.get('/api/session/session/executions')
        assert response.status_code == 200
        assert response.json()['branches'][0]['execution_ids'] == ['run']
        assert client.get('/api/session/other/executions').status_code == 404


def test_compaction_coverage_retains_earlier_turn_membership(tmp_path):
    from openprogram.execution.activity_branches import conversation_activity_branches
    db = SessionStore(tmp_path / 'sessions')
    writer = SessionNodeWriter(db, 's')
    writer.append(Call(id='u1', role='user', predecessor='ROOT'))
    writer.append(Call(id='a1', role='llm', predecessor='u1'))
    writer.append(Call(id='u2', role='user', predecessor='a1'))
    writer.append(Call(id='a2', role='llm', predecessor='u2'))
    writer.append(Call(id='summary_one', role='llm', name='context/summary', predecessor='ROOT',
                       metadata={'covers_ids': ['u1', 'a1']}))
    writer.update('u2', predecessor='summary_one')
    # The original prefix remains durable, but its consumed tip is not a branch.
    db.mark_merged('s', ['a1'])
    items = [{'execution_id': key, 'session_id': 's', 'snapshot': {'display': {
        'user_message_id': user, 'assistant_message_id': assistant}}}
        for key, user, assistant in [('e1', 'u1', 'a1'), ('e2', 'u2', 'a2')]]
    branches = conversation_activity_branches(items, session_store=db)
    assert len(branches) == 1
    assert set(branches[0]['execution_ids']) == {'e1', 'e2'}
