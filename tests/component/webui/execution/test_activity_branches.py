from openprogram.state.store import SessionNodeWriter
from openprogram.state.store.session.session_store import SessionStore
from openprogram.state.store.session.nodes import Call


def test_activity_projects_conversation_branches_not_turns(tmp_path):
    from openprogram.runtime.execution.activity_branches import conversation_activity_branches

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
    from openprogram.runtime.execution.activity_branches import conversation_activity_branches
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
    from openprogram.interfaces.server.routes.execution import lifecycle
    db = SessionStore(tmp_path / 'sessions')
    writer = SessionNodeWriter(db, 'session')
    writer.append(Call(id='user', role='user', predecessor='ROOT'))
    writer.append(Call(id='assistant', role='llm', predecessor='user'))
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: db)
    monkeypatch.setattr('openprogram.runtime.execution.default_store', lambda: SimpleNamespace(get_agent_turn_input=lambda _: {}))
    execution = SimpleNamespace(execution_id='run', session_id='session', status=SimpleNamespace(value='completed'), created_at=1, status_version=1)
    monkeypatch.setattr('openprogram.runtime.execution.conversation_scope.conversation_execution_scope', lambda *_: ([execution], {}))
    monkeypatch.setattr(lifecycle, '_execution_payload', lambda _: {'display': {'user_message_id': 'user', 'assistant_message_id': 'assistant'}})
    monkeypatch.setattr(lifecycle, '_actor_and_session', lambda _: ({}, 'session'))
    monkeypatch.setattr('openprogram.runtime.execution.authorization.authorize_session_action', lambda *_: None)
    app = FastAPI()
    lifecycle.register(app)
    with TestClient(app) as client:
        response = client.get('/api/session/session/executions')
        assert response.status_code == 200
        assert response.json()['branches'][0]['execution_ids'] == ['run']
        assert client.get('/api/session/other/executions').status_code == 404


def test_compaction_coverage_retains_earlier_turn_membership(tmp_path):
    from openprogram.runtime.execution.activity_branches import conversation_activity_branches
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


def test_branch_projection_observes_placement_from_another_store(tmp_path):
    from openprogram.runtime.execution.activity_branches import conversation_activity_branches
    root = tmp_path / 'home-sessions'
    reader = SessionStore(root)
    registrar = SessionStore(root)
    # Inside the home store: a conversation folder recorded under another
    # store's root belongs to a copied state and is not followed.
    project = SessionStore(root / 'project-sessions')
    writer = SessionNodeWriter(project, 'placed')
    writer.append(Call(id='user', role='user', predecessor='ROOT'))
    writer.append(Call(id='assistant', role='llm', predecessor='user'))
    registrar._record_location('placed', project.root_path / 'default' / 'chats' / 'placed')
    items = [{'execution_id': 'run', 'session_id': 'placed', 'snapshot': {'display': {
        'user_message_id': 'user', 'assistant_message_id': 'assistant'}}}]
    branches = conversation_activity_branches(items, session_store=reader)
    assert len(branches) == 1
    assert branches[0]['execution_ids'] == ['run']


def test_branch_projection_observes_relocation_after_open(tmp_path):
    from openprogram.runtime.execution.activity_branches import conversation_activity_branches
    reader = SessionStore(tmp_path / 'home')
    registrar = SessionStore(tmp_path / 'home')
    for place, tip in [('old', 'old-tip'), ('new', 'new-tip')]:
        project = SessionStore(tmp_path / 'home' / place)
        writer = SessionNodeWriter(project, 'placed')
        writer.append(Call(id='user', role='user', predecessor='ROOT'))
        writer.append(Call(id=tip, role='llm', predecessor='user'))
        registrar._record_location('placed', project.root_path / 'default' / 'chats' / 'placed')
        items = [{'execution_id': tip, 'session_id': 'placed', 'snapshot': {'display': {
            'user_message_id': 'user', 'assistant_message_id': tip}}}]
        branches = conversation_activity_branches(items, session_store=reader)
        assert [branch['head_msg_id'] for branch in branches] == [tip]


def test_spawned_agent_branch_is_named_by_its_task_label_while_it_runs(tmp_path, monkeypatch):
    """A background agent's branch shows its task's label from admission on:
    in the branch list (list_agents reads it) and in Activity, before the
    run has replied. A message to that agent later keeps the name."""
    from types import SimpleNamespace
    from openprogram.runtime.execution.activity_branches import conversation_activity_branches
    from openprogram.runtime.jobs.runner import admission

    db = SessionStore(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: db)
    db.create_session('c', 'main', title='t')
    db.append_message('c', {'id': 'u1', 'role': 'user', 'content': 'go', 'predecessor': ''})
    db.append_message('c', {'id': 'a1', 'role': 'assistant', 'content': 'spawning', 'predecessor': 'u1'})
    db.set_branch_name('c', 'a1', 'Main line', name_locked=True)

    job = SimpleNamespace(id='j1', parent_session_id='c', label='s1', target_branch_head_id=None,
                          parent_msg_id='a1')
    admission._name_job_branch(job, creates_agent=True)
    db.spawn_branch('c', 'a1', source='agent_spawn', node_id='j1_user', prompt='work',
                    register_head=False)
    SessionNodeWriter(db, 'c', advance_head=False).append(
        Call(id='j1_user_reply', role='llm', predecessor='j1_user'))

    names = {row['head_msg_id']: row['name'] for row in db.list_branches('c')}
    assert names['j1_user_reply'] == 's1'
    items = [{'execution_id': 'j1', 'session_id': 'c', 'snapshot': {
        'display': {'user_message_id': 'j1_user', 'assistant_message_id': 'j1_user_reply'}}}]
    branches = conversation_activity_branches(items, conversation_session_id='c', session_store=db)
    assert [(b['head_msg_id'], b['name']) for b in branches] == [('j1_user_reply', 's1')]

    # The turn's end keeps the main line's record where it is.
    db.carry_branch_entry('c', 'j1_user_reply')
    assert db.get_branch_meta('c', 'a1')['name'] == 'Main line'
    # A rename while it runs outlives the label written again at the end,
    # and a message to the agent opens no second name.
    db.set_branch_name('c', 'j1_user_reply', 'Renamed', name_locked=True)
    db.name_spawned_branch('c', 'j1_user_reply', 's1')
    admission._name_job_branch(SimpleNamespace(
        id='j2', parent_session_id='c', label='hop', target_branch_head_id=None,
        parent_msg_id='j1_user_reply'), creates_agent=True)
    assert 'j2_user_reply' not in db.branch_entries('c')
    assert db.get_branch_meta('c', 'j1_user_reply')['name'] == 'Renamed'
