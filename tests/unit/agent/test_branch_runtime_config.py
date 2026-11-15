"""An admitted branch keeps its configuration when the page changes branch."""
from types import SimpleNamespace

from openprogram.runtime.agent.turn_request import TurnRequest, set_turn_request, reset_turn_request
from openprogram.runtime.conversation import session_model
from openprogram.runtime.permissions.lifecycle import current_permission_request


def test_nested_model_uses_admitted_model_not_page(monkeypatch):
    monkeypatch.setattr(session_model, '_read_memory_pin', lambda _: ('provider', 'page-b'))
    request = TurnRequest('s', '', 'main', 'agent_spawn', model_override='provider/branch-a')
    token = set_turn_request(request)
    try:
        assert session_model.read_session_chat_model('s') == ('provider', 'branch-a')
    finally:
        reset_turn_request(token)


def test_live_permission_refresh_names_owning_branch(monkeypatch):
    import openprogram.runtime.permissions.lifecycle as lifecycle
    import openprogram.runtime.permissions.rule as rule
    seen = []
    def state(sid, **kwargs):
        seen.append(kwargs.get('branch_id'))
        return {'mode': 'ask' if kwargs.get('branch_id') == 'a' else 'bypass', 'version': 2, 'principal_id': 'owner'}
    monkeypatch.setattr(lifecycle, 'permission_state', state)
    monkeypatch.setattr(rule, 'load_merged_rules', lambda *args, **kwargs: None)
    request = SimpleNamespace(session_id='s', source='web', authority_tier='owner', interaction='interactive',
                              principal_id='owner', permission_mode='ask', permission_rules=None, branch_id='a')
    result = current_permission_request(request)
    assert result.permission_mode == 'ask'
    assert seen == ['a']


def test_canonical_admission_freezes_page_config_before_switch(monkeypatch):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    row = {'active_branch_id': 'a', 'provider_override': 'p', 'model_override': 'a',
           'thinking_effort': 'high', 'web_search': False}
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db',
                        lambda: SimpleNamespace(get_session=lambda _: row))
    adapter = object.__new__(CanonicalAgentAdapter)
    adapter.admit_payload = lambda **kwargs: kwargs['payload']
    request = TurnRequest('s', 'hello', 'main', 'web')
    payload = adapter.admit(request, trusted_actor={}, user_message_id=None, config_snapshot_ref='test')
    row.update(active_branch_id='b', model_override='b', thinking_effort='low')
    assert payload['request']['branch_id'] == 'a'
    assert payload['request']['model_override'] == 'p/a'
    assert payload['request']['thinking_effort'] == 'high'
    assert payload['request']['tools_override'] == {'inherit': True, 'web_search': False}


def test_durable_child_inherits_parent_not_selected_branch(monkeypatch):
    from openprogram.runtime.jobs.input import JobAgentInputV1
    from openprogram.runtime.jobs.types import Job
    row = {'active_branch_id': 'b', 'provider_override': 'p', 'model_override': 'b',
           'branch_refs': {'a': {'config': {'provider_override': 'p', 'model_override': 'changed-a'}}}}
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db',
                        lambda: SimpleNamespace(get_session=lambda _: row))
    parent = TurnRequest('s', '', 'main', 'web', branch_id='a', model_override='p/frozen-a',
                         thinking_effort='high', tools_override=['read'],
                         sandbox_enabled=True, sandbox_config_frozen=True)
    token = set_turn_request(parent)
    try:
        payload = JobAgentInputV1.from_job(Job('j_child', 's', 'work', 'main')).to_dict()
    finally:
        reset_turn_request(token)
    row['branch_refs']['a']['config']['model_override'] = 'again'
    request = JobAgentInputV1.parse(payload).to_turn_request()
    assert request.branch_id == 'a'
    assert request.model_override == 'p/frozen-a'
    assert request.tools_override == ['read']
    assert request.thinking_effort == 'high'
    assert request.sandbox_enabled is True
    assert request.sandbox_config_frozen is True


def test_copy_inactive_branch_uses_its_agent_config(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context.branch_session import branch_session
    from tests.unit.runtime.test_branch_session import _seed
    store = SessionStore(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: store)
    try:
        _seed(store)
        row = store.get_session('src')
        refs = dict(row.get('branch_refs') or {})
        target_id = next((key for key, ref in refs.items() if ref.get('head_id') == 'u2b_reply'), 'inactive')
        refs[target_id] = {'head_id': 'u2b_reply', 'config': {'agent_id': 'other', 'model_override': 'other-model', 'provider_override': 'p', 'thinking_effort': 'low'}}
        store.update_session('src', branch_refs=refs)
        copied = branch_session('src', 'u2b_reply', store=store)
        config = store.get_session(copied)
        assert config['agent_id'] == 'other'
        assert config['model_override'] == 'other-model'
        assert config['thinking_effort'] == 'low'
    finally:
        store.close()


def test_delivery_to_existing_sibling_uses_destination_settings(monkeypatch):
    from openprogram.runtime.conversation.branch_config import freeze_branch_request
    row = {'active_branch_id': 'a', 'model_override': 'a', 'provider_override': 'p',
           'branch_refs': {'b': {'head_id': 'tip-b', 'config': {'provider_override': 'p', 'model_override': 'b'}}}}
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db',
                        lambda: SimpleNamespace(get_session=lambda _: row))
    parent = TurnRequest('s', '', 'main', 'web', branch_id='a', model_override='p/a')
    token = set_turn_request(parent)
    try:
        frozen = freeze_branch_request({'session_id': 's', 'branch_from': 'tip-b'})
    finally:
        reset_turn_request(token)
    assert frozen['branch_id'] == 'b'
    assert frozen['model_override'] == 'p/b'


def test_admission_inherit_cannot_claim_pending_fork_identity(monkeypatch):
    import pytest
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.state.store.session.branch_config import BranchConfigConflict, branch_identity
    row = {'active_branch_id': 'a', 'head_id': 'tip-a', 'model_override': 'a', 'provider_override': 'p'}
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db',
                        lambda: SimpleNamespace(get_session=lambda _: row, message_exists=lambda *args: False))
    adapter = object.__new__(CanonicalAgentAdapter)
    adapter.admit_payload = lambda **kwargs: kwargs['payload']
    request = TurnRequest('s', 'hello', 'main', 'web', user_msg_id='future-user',
                          branch_id=branch_identity('s', 'future-user'))
    with pytest.raises(BranchConfigConflict):
        adapter.admit(request, trusted_actor={}, user_message_id='future-user', config_snapshot_ref='test')
    # An explicit planned fork is the only valid missing-ref case.
    request.branch_from = 'ROOT'
    payload = adapter.admit(request, trusted_actor={}, user_message_id='future-user', config_snapshot_ref='test')
    assert payload['request']['branch_id'] == branch_identity('s', 'future-user')


# Use the real persisted session fixture for public copy and Job admission.
from tests.unit.dag.test_rewind_modes import store


def test_copy_running_branch_keeps_selected_settings(store, monkeypatch):
    import asyncio
    from tests.unit.dag.test_rewind_modes import Session, _append
    from tests.unit.dag.test_shared_workspace_rewind import _checkout_server
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes.chat_branches import post_chat_branch_session
    from openprogram.runtime.context.branch_session import branch_session, BranchSessionError
    import pytest
    _checkout_server(monkeypatch, store)
    session = Session(store, 's'); session.turn_edits(); session.turn_edits()
    store.update_session('s', model_override='running-model')
    store.update_node('s', 'a2', status='streaming')
    branch = store.get_session('s')['active_branch_id']
    monkeypatch.setattr(server, '_is_run_active', lambda sid: True)
    response = asyncio.run(post_chat_branch_session({'session_id': 's', 'branch_id': branch}))
    assert response.status_code == 200
    import json
    result = json.loads(response.body)
    assert result['msg_id'] == 'a1'
    assert store.get_session(result['new_session_id'])['model_override'] == 'running-model'
    _append(store, 's', 'other-user', 'user', 'ROOT')
    _append(store, 's', 'other-reply', 'assistant', 'other-user')
    with pytest.raises(BranchSessionError, match='does not belong'):
        branch_session('s', 'other-reply', branch_id=branch, store=store)


def test_owner_job_permission_comes_from_destination_and_replays(store, monkeypatch):
    from tests.unit.dag.test_rewind_modes import Session, _append
    from tests.unit.dag.test_branch_config import checkout
    from tests.unit.dag.test_shared_workspace_rewind import _checkout_server
    from openprogram.runtime.jobs.input import JobAgentInputV1
    from openprogram.runtime.jobs.types import Job
    from openprogram.runtime.permissions.authority import local_owner_authority
    _checkout_server(monkeypatch, store)
    session = Session(store, 's'); session.turn_edits()
    store.update_session('s', permission_mode='bypass', model_override='a', provider_override='p')
    _append(store, 's', 'ub', 'user', 'ROOT'); _append(store, 's', 'ab', 'assistant', 'ub')
    checkout('ab')
    destination = store.get_session('s')['active_branch_id']
    store.update_session('s', permission_mode='ask', model_override='b', permission_rules={'deny': ['write_file']})
    checkout('a1')
    job = Job('j_delivery', 's', 'work', 'main', parent_msg_id='ab', **local_owner_authority())
    payload = JobAgentInputV1.from_job(job).to_dict()
    request = payload['turn_request']
    assert request['branch_id'] == destination
    assert request['permission_mode'] == 'ask'
    assert 'write_file' in request['permission_rules']['deny']
    # Replay never consults whatever branch the page now shows.
    store.update_session('s', permission_mode='bypass')
    replay = JobAgentInputV1.parse(payload).to_turn_request()
    assert replay.permission_mode == 'ask'
    assert 'write_file' in replay.permission_rules.deny
    inherited = JobAgentInputV1.from_job(job, permission_snapshot={'mode': 'plan', 'rules': {'deny': ['bash']}})
    assert inherited.turn_request['permission_mode'] == 'plan'
    assert inherited.turn_request['permission_rules'] == {'deny': ['bash']}


import pytest


@pytest.mark.parametrize('enabled,other', [(True, False), (False, True), (None, False)])
@pytest.mark.parametrize('durable', [False, True])
def test_turn_binding_uses_owning_sandbox_choice(store, monkeypatch, enabled, other, durable):
    from tests.unit.dag.test_rewind_modes import Session, _append
    from tests.unit.dag.test_shared_workspace_rewind import _checkout_server
    from tests.unit.dag.test_branch_config import checkout
    from openprogram.runtime.jobs.input import JobAgentInputV1
    from openprogram.runtime.jobs.types import Job
    from openprogram.runtime.conversation.turn_runtime.turn_context import TurnBindings
    import openprogram.host.sandbox as sandbox
    _checkout_server(monkeypatch, store)
    monkeypatch.setattr('openprogram.runtime.conversation.session_config.project_defaults', lambda _: {})
    session = Session(store, 's'); session.turn_edits()
    if enabled is not None:
        store.update_session('s', sandbox_enabled=enabled)
    branch = store.get_session('s')['active_branch_id']
    payload = JobAgentInputV1.from_job(Job('j_sandbox', 's', 'work', 'main', model_override='invalid/no-provider')).to_dict()
    assert payload['turn_request']['sandbox_config_frozen'] is True
    assert payload['turn_request']['sandbox_enabled'] is enabled
    _append(store, 's', 'ub', 'user', 'ROOT'); _append(store, 's', 'ab', 'assistant', 'ub')
    checkout('ab'); store.update_session('s', sandbox_enabled=other)
    observed = []
    real_bind = sandbox.bind_turn_policy
    def capture(value=None):
        observed.append(value)
        return real_bind(value)
    monkeypatch.setattr(sandbox, 'bind_turn_policy', capture)
    if durable:
        request = JobAgentInputV1.parse(payload).to_turn_request()
    else:
        # Legacy callers have branch identity but no admitted sandbox field.
        request = TurnRequest('s', 'work', 'main', 'agent_spawn', branch_id=branch,
                              model_override='invalid/no-provider', advance_head=False)
    bindings = TurnBindings.bind(req=request, assistant_msg_id='background', db=store,
                                 snapshot_project_baseline=False)
    try:
        assert observed == [enabled]
    finally:
        bindings.release()


def test_admitted_sandbox_inherit_is_not_reread_from_branch(store, monkeypatch):
    from tests.unit.dag.test_rewind_modes import Session
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.conversation.turn_runtime.turn_context import TurnBindings
    import openprogram.host.sandbox as sandbox
    monkeypatch.setattr('openprogram.runtime.conversation.session_config.project_defaults', lambda _: {})
    session = Session(store, 's'); session.turn_edits()
    adapter = object.__new__(CanonicalAgentAdapter)
    adapter.admit_payload = lambda **kwargs: kwargs['payload']
    request = TurnRequest('s', 'work', 'main', 'web', model_override='invalid/no-provider')
    payload = adapter.admit(request, trusted_actor={}, user_message_id=None, config_snapshot_ref='test')
    assert payload['request']['sandbox_config_frozen'] is True
    assert payload['request']['sandbox_enabled'] is None
    store.update_session('s', sandbox_enabled=False)
    observed = []
    monkeypatch.setattr(sandbox, 'bind_turn_policy', lambda value=None: observed.append(value))
    replay = TurnRequest(**payload['request'])
    bindings = TurnBindings.bind(req=replay, assistant_msg_id='background', db=store,
                                 snapshot_project_baseline=False)
    try:
        assert observed == [None]
    finally:
        bindings.release()


def test_conversation_with_no_branch_recorded_runs_on_its_own_settings(monkeypatch):
    """A scheduled task's first run lands in a conversation no branch was recorded for."""
    import pytest
    from openprogram.runtime.conversation.branch_config import freeze_branch_request
    from openprogram.state.store.session.branch_config import BranchConfigConflict, config_for_branch
    row = {'id': 'cron-1', 'head_id': None, 'sandbox_enabled': False, 'thinking_effort': 'low'}
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db',
                        lambda: SimpleNamespace(get_session=lambda _: row, message_exists=lambda *_: False))
    frozen = freeze_branch_request({'session_id': 'cron-1', 'user_msg_id': 'm1', 'branch_from': None})
    assert config_for_branch(row, frozen['branch_id'])['thinking_effort'] == 'low'
    recorded = {**row, 'active_branch_id': 'a'}
    with pytest.raises(BranchConfigConflict):
        config_for_branch(recorded, frozen['branch_id'])
