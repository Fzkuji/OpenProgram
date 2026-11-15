"""Web Agent selection and trial binding through durable chat admission."""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from tests.support.thread_scope import fake_threads_started_by


class Socket:
    def __init__(self):
        self.frames = []

    async def send_text(self, value):
        self.frames.append(json.loads(value))


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import chat
    from openprogram.runtime.execution import ExecutionStore, RuntimeControlService, AttemptStore, DriverRegistry

    executions = ExecutionStore(tmp_path / 'executions.sqlite3')
    control = RuntimeControlService(executions, AttemptStore(executions), DriverRegistry())
    monkeypatch.setattr('openprogram.runtime.execution.default_store', lambda: executions)
    monkeypatch.setattr('openprogram.runtime.execution.default_control_service', lambda: control)
    (tmp_path / 'state').mkdir()
    # The turns these tests run record the last used model in config.json;
    # in the worker's shared home that record would make every later test's
    # new conversation start pinned to this test's model.
    from openprogram.state import paths
    monkeypatch.setattr(paths, 'get_state_dir', lambda: tmp_path / 'state')
    monkeypatch.setattr(manager, '_state_root', lambda: tmp_path / 'state')
    manager.create('main', name='Default', make_default=True)
    manager.create('research', name='Research')
    manager.update('research', {
        'model': {'provider': 'openai', 'id': 'gpt-4o'},
        'thinking_effort': 'high', 'system_prompt': 'SAVED_PROMPT',
        'memory': {'mode': 'read_write', 'read_spaces': ['self'], 'write_space': 'self', 'required': False},
        'tools': {'mode': 'selected', 'allowed': ['read']},
    }, replace_tool_policy=True)
    db = SessionDB(tmp_path / 'sessions')
    databases = [db]
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: db)
    monkeypatch.setattr(server, '_sessions', {})
    monkeypatch.setattr(server, '_running_tasks', {})
    monkeypatch.setattr(server, '_msg_cache', {})
    monkeypatch.setattr('openprogram.runtime.conversation.run_control.active_runtimes._active_exec_runtimes', {})
    monkeypatch.setattr(server, '_user_pinned_provider', 'unrelated')
    monkeypatch.setattr(server, '_user_pinned_model', 'global-model')
    monkeypatch.setattr(server, '_emit_running_task_event', lambda *a, **k: None)
    monkeypatch.setattr('openprogram.interfaces.server.ws_actions.session.broadcast_sessions_list', lambda: None)
    monkeypatch.setattr('openprogram.runtime.conversation.surface_context.capture', lambda *a, **k: None)
    class Thread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def is_alive(self):
            return True
    fake_threads_started_by(monkeypatch, 'openprogram.interfaces.server.ws_actions.chat.turn_launch', Thread)
    requests = []
    admissions = []
    original = CanonicalAgentAdapter.admit
    def admit(self, request, **kwargs):
        result = original(self, request, **kwargs)
        requests.append(request)
        admissions.append((self, result))
        return result
    monkeypatch.setattr(CanonicalAgentAdapter, 'admit', admit)
    def send(session_id='local_test', **fields):
        ws = Socket()
        asyncio.run(chat.handle_chat(ws, {'text': 'hello', 'session_id': session_id, **fields}))
        # No provider is activated in these admission tests. Finish the owned
        # durable record so follow-up turns can exercise persisted binding.
        for adapter, admission in admissions:
            adapter.fail_admission(admission, reason_code='test_no_activation')
        admissions.clear()
        for sid, task in list(server._running_tasks.items()):
            server._finish_owned_run(sid, task['msg_id'])
        return ws
    def restart():
        nonlocal db
        server._sessions.clear()
        db = SessionDB(tmp_path / 'sessions')
        databases.append(db)
    yield SimpleNamespace(db=db, manager=manager, server=server, requests=requests, send=send, restart=restart)
    import atexit
    for database in databases:
        database._flush_index()
        atexit.unregister(database._flush_index)


def test_selected_saved_agent_controls_first_turn_not_global_defaults(invocation):
    h = invocation
    before = h.manager.get('research').to_dict()
    ws = h.send(agent_id='research', thinking_effort='low', tools=False)
    assert any(f['type'] == 'chat_ack' for f in ws.frames), ws.frames
    request = h.requests[-1]
    assert request.agent_id == 'research'
    assert request.model_override == 'openai/gpt-4o'
    assert request.thinking_effort == 'high'
    assert request.tools_override is None
    assert h.db.get_session('local_test')['agent_id'] == 'research'
    assert h.manager.get('research').to_dict() == before


def test_trial_snapshot_survives_restart_and_cannot_rebind(invocation):
    from openprogram.runtime.conversation.session_config import load_agent_session_binding
    h = invocation
    before = h.manager.get('research').to_dict()
    draft = {'model': {'provider': 'openai', 'id': 'gpt-4o-mini'},
             'thinking_effort': 'low', 'system_prompt': 'DRAFT_PROMPT',
             'tools': {'mode': 'none'},
             'skills': {'disabled': ['*'], 'allowed': [], 'categories': []},
             'mcp': {'disabled': ['*'], 'allowed': [], 'required': []}}
    ws = h.send(agent_id='research', agent_trial=True, agent_config=draft)
    assert any(f['type'] == 'chat_ack' for f in ws.frames), ws.frames
    first = h.requests[-1]
    assert first.profile_snapshot['system_prompt'] == 'DRAFT_PROMPT'
    assert first.profile_snapshot['tools'] == {'mode': 'none'}
    assert first.profile_snapshot['skills']['disabled'] == ['*']
    assert first.model_override == 'openai/gpt-4o-mini'
    assert first.memory_policy_override == {'mode': 'read_only'}
    snapshot = copy.deepcopy(first.profile_snapshot)
    draft['system_prompt'] = 'MUTATED'
    h.server._running_tasks.clear()
    h.restart()
    h.manager.update('research', {'system_prompt': 'LATER_SAVED_PROMPT'})
    binding = load_agent_session_binding('local_test')
    assert binding['profile_snapshot'] == snapshot
    h.send()
    assert h.requests[-1].profile_snapshot == snapshot
    assert h.requests[-1].memory_policy_override == {'mode': 'read_only'}
    h.server._running_tasks.clear()
    denied = h.send(agent_id='main')
    assert denied.frames[0]['data']['code'] == 'agent_session_bound'
    assert h.db.get_session('local_test')['agent_id'] == 'research'
    assert h.manager.get('research').model.id == before['model']['id']


@pytest.mark.parametrize('fields,code', [
    ({'agent_id': 'unknown'}, 'agent_not_found'),
    ({'agent_id': 'research', 'agent_trial': True, 'agent_config': {'permission_mode': 'bypass'}}, 'invalid_agent_config'),
    ({'agent_id': 'research', 'agent_config': {'system_prompt': 'BAD'}}, 'invalid_agent_config'),
])
def test_invalid_invocation_does_not_create_session(invocation, fields, code):
    h = invocation
    ws = h.send(**fields)
    assert ws.frames[0]['data']['code'] == code
    assert ws.frames[0]['data']['msg_id']
    assert not h.requests
    assert h.db.get_session('local_test') is None


def test_saved_invocation_model_and_effort_override_are_session_only(invocation):
    h = invocation
    ws = h.send(agent_id='research', agent_overrides={
        'model': {'provider': 'openai', 'id': 'gpt-4o-mini'}, 'thinking_effort': 'low',
    })
    assert h.requests, ws.frames
    request = h.requests[-1]
    assert request.model_override == 'openai/gpt-4o-mini'
    assert request.thinking_effort == 'low'
    assert h.manager.get('research').model.id == 'gpt-4o'
    assert h.manager.get('research').thinking_effort == 'high'


def test_trial_chat_reaches_provider_with_draft_prompt_model_and_no_tools(invocation, monkeypatch):
    """The admitted draft executes the real dispatcher/loop against a fake stream."""
    from openprogram.runtime.conversation import turn_runtime as D
    from openprogram.models.providers.types import AssistantMessage, EventDone, Model, TextContent, Usage
    from openprogram.state.memory import set_backend

    h = invocation
    h.send(agent_id='research', agent_trial=True, agent_config={
        'model': {'provider': 'openai', 'id': 'gpt-4o-mini'},
        'thinking_effort': 'low', 'system_prompt': 'UNSAVED_INSTRUCTION',
        'tools': {'mode': 'none'}, 'skills': {'allowed': [], 'disabled': ['*']},
    })
    request = h.requests[-1]
    observed = []
    model = Model(id='gpt-4o-mini', name='fake', api='completion', provider='openai', base_url='https://invalid.example', reasoning=True, thinking_levels=['low', 'high'])
    monkeypatch.setattr(D, '_resolve_model', lambda _profile, override=None: model)
    monkeypatch.setattr(D, '_memory_write', lambda _: None)
    async def stream(selected, context, options):
        observed.append((selected, context, options))
        yield EventDone(reason='stop', message=AssistantMessage(
            content=[TextContent(text='draft response')], api='completion', provider='openai',
            model=selected.id, usage=Usage(input=1, output=1), stop_reason='stop', timestamp=1,
        ))
    original = D._run_loop_blocking
    monkeypatch.setattr(D, '_run_loop_blocking', lambda **kw: original(**kw, stream_fn=stream))
    set_backend(None)
    try:
        result = D.process_user_turn(request)
    finally:
        set_backend(None)
    assert not result.failed, result.error
    assert result.final_text == 'draft response'
    assert len(observed) == 1
    selected, context, options = observed[0]
    assert selected.id == 'gpt-4o-mini'
    assert 'UNSAVED_INSTRUCTION' in context.system_prompt
    assert not context.tools
    assert options.reasoning == 'low'
    assert [row['role'] for row in h.db.get_branch('local_test')] == ['user', 'assistant']
    assert h.manager.get('research').system_prompt == 'SAVED_PROMPT'


def test_cleared_trial_cannot_be_converted_to_writable_saved_agent(invocation):
    from openprogram.runtime.conversation.session_config import load_agent_session_binding
    h = invocation
    h.send(agent_id='research', agent_trial=True, agent_config={'system_prompt': 'TRIAL'})
    # A cleared/reconstructed transcript must not erase its persistent mode.
    h.db.set_head('local_test', None)
    h.send(agent_id='research', agent_trial=False)
    assert load_agent_session_binding('local_test')['memory_policy_override'] == {'mode': 'read_only'}
    denied = h.send(agent_id='research', agent_trial=True, agent_config={'system_prompt': 'CHANGED'})
    assert denied.frames[0]['data']['code'] == 'agent_session_bound'
    assert load_agent_session_binding('local_test')['profile_snapshot']['system_prompt'] == 'TRIAL'


@pytest.mark.parametrize('tools,override', [
    ({'mode': 'automatic'}, None), ({'mode': 'none'}, None), ({'mode': 'automatic'}, []),
])
def test_required_mcp_unavailable_rejects_before_provider(invocation, monkeypatch, tools, override):
    from openprogram.runtime.conversation import turn_runtime as D
    from openprogram.models.providers.types import AssistantMessage, EventDone, Model, TextContent
    h = invocation
    h.send(agent_id='research', agent_trial=True, agent_config={
        'tools': tools, 'mcp': {'allowed': [], 'disabled': [], 'required': ['missing_test_server']},
    })
    request = h.requests[-1]
    request.tools_override = override
    calls = []
    monkeypatch.setattr(D, '_resolve_model', lambda *_: Model(
        id='fake', name='fake', api='completion', provider='openai', base_url='https://invalid.example'))
    monkeypatch.setattr(D, '_memory_write', lambda _: None)
    async def stream(*_):
        calls.append('provider')
        yield EventDone(reason='stop', message=AssistantMessage(
            content=[TextContent(text='must not run')], api='completion', provider='openai',
            model='fake', stop_reason='stop', timestamp=1,
        ))
    original = D._run_loop_blocking
    monkeypatch.setattr(D, '_run_loop_blocking', lambda **kw: original(**kw, stream_fn=stream))
    result = D.process_user_turn(request)
    assert result.failed
    assert 'Required MCP servers unavailable: missing_test_server' in result.error
    assert calls == []


@pytest.mark.parametrize('trial', [False, True])
def test_inherited_model_pins_enabled_global_default_without_rewriting_config(invocation, monkeypatch, trial):
    h = invocation
    h.server._user_pinned_provider = 'openai'
    h.server._user_pinned_model = 'gpt-4o'
    monkeypatch.setattr(h.server.runtime_management, '_default_is_enabled', lambda p, m: (p, m) == ('openai', 'gpt-4o'))
    if trial:
        fields = {'agent_trial': True, 'agent_config': {'model': {'provider': '', 'id': ''}, 'tools': {'mode': 'none'}}}
    else:
        h.manager.update('research', {'model': {'provider': '', 'id': ''}})
        fields = {}
    ws = h.send(agent_id='research', **fields)
    assert any(frame['type'] == 'chat_ack' for frame in ws.frames), ws.frames
    assert h.requests[-1].model_override == 'openai/gpt-4o'
    assert h.requests[-1].profile_snapshot['model'] == {'provider': '', 'id': ''}
    assert h.manager.get('research').model.id == ('gpt-4o' if trial else '')
    from openprogram.runtime.conversation import turn_runtime as D
    from openprogram.models.providers.types import AssistantMessage, EventDone, TextContent, Model
    model = Model(id='gpt-4o', name='fixture model', api='completion', provider='openai', base_url='https://invalid.example')
    monkeypatch.setattr('openprogram.models.providers.catalog.models.get_model',
                        lambda provider, name: model if (provider, name) == ('openai', 'gpt-4o') else None)
    calls = []
    async def stream(model, _context, _options):
        calls.append((model.provider, model.id))
        yield EventDone(reason='stop', message=AssistantMessage(
            content=[TextContent(text='inherited reply')], api='completion',
            provider=model.provider, model=model.id, stop_reason='stop', timestamp=1,
        ))
    original = D._run_loop_blocking
    monkeypatch.setattr(D, '_run_loop_blocking', lambda **kw: original(**kw, stream_fn=stream))
    monkeypatch.setattr(D, '_memory_write', lambda _: None)
    result = D.process_user_turn(h.requests[-1])
    assert not result.failed, result.error
    assert calls == [('openai', 'gpt-4o')]


def test_disabled_inherited_model_rejects_without_creating_session(invocation, monkeypatch):
    h = invocation
    monkeypatch.setattr(h.server.runtime_management, '_default_is_enabled', lambda *_: False)
    ws = h.send(agent_id='research', agent_trial=True, agent_config={'model': {'provider': '', 'id': ''}})
    assert ws.frames[0]['data']['code'] == 'agent_model_unavailable'
    assert ws.frames[0]['data']['msg_id']
    assert not h.requests
    assert h.db.get_session('local_test') is None


def test_existing_empty_saved_session_binds_the_first_message_agent(invocation):
    # New chat creates the row before the first message; the composer's Agent
    # pick then rides that message and binds the still-empty conversation.
    h = invocation
    h.db.create_session('local_test', 'main')
    ws = h.send(agent_id='research')
    assert not [f for f in ws.frames if f['data'].get('type') == 'error'], ws.frames
    assert h.requests
    assert h.db.get_session('local_test')['agent_id'] == 'research'
    # Once something was said (the first message is in the transcript now),
    # the conversation keeps its Agent.
    assert h.db.get_messages('local_test')
    ws = h.send(agent_id='main')
    assert ws.frames[0]['data']['code'] == 'agent_session_bound'


def test_existing_empty_saved_session_cannot_become_trial(invocation):
    h = invocation
    h.db.create_session('local_test', 'research')
    ws = h.send(agent_id='research', agent_trial=True, agent_config={'system_prompt': 'TRIAL'})
    assert ws.frames[0]['data']['code'] == 'agent_session_bound'
    assert not h.requests


def test_complete_editor_dto_preserves_server_identity_and_partial_memory_merges(invocation):
    h = invocation
    saved = h.manager.get('research').to_dict()
    # What the server owns and the editor never sends back: identity, revision
    # bookkeeping and the catalog role the Agent was created from.
    immutable = {'id', 'revision', 'created_at', 'updated_at', 'default', 'memory_policy_epoch', 'role'}
    config = {key: value for key, value in saved.items() if key not in immutable}
    config['memory'] = {'mode': 'read_only'}
    ws = h.send(agent_id='research', agent_trial=True, agent_config=config)
    assert any(frame['type'] == 'chat_ack' for frame in ws.frames), ws.frames
    snapshot = h.requests[-1].profile_snapshot
    assert snapshot['created_at'] == saved['created_at']
    assert snapshot['memory_policy_epoch'] == saved['memory_policy_epoch']
    assert snapshot['memory'] == {**saved['memory'], 'mode': 'read_only'}
    assert h.manager.get('research').memory == saved['memory']
