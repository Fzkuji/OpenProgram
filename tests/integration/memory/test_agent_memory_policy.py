"""Agent memory policy through real dispatcher persistence and idle writing."""
from types import SimpleNamespace
import atexit

import pytest
from openprogram.runtime.agent import turn_request as agent_turn_request


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from openprogram.runtime.conversation import turn_runtime as D
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.state.memory import set_backend
    from openprogram.models.providers.types import Model

    state = tmp_path / 'state'
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: state)
    monkeypatch.setattr('openprogram.state.config.read_config', lambda: {})
    db = SessionDB(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: db)
    profile = {'id': 'private', 'memory': {'mode': 'off', 'read_spaces': ['self'], 'write_space': 'self', 'required': False}, 'memory_policy_epoch': 1}
    # A registry spec carries the model, tools and effort a branch inherits.
    monkeypatch.setattr(manager, 'get', lambda _id: SimpleNamespace(**{
        'model': SimpleNamespace(provider='openai', id='stub'), 'tools': None, 'thinking_effort': None, **profile}))
    monkeypatch.setattr(D, '_load_agent_profile', lambda _id: dict(profile))
    monkeypatch.setattr(D, '_resolve_model', lambda *_: Model(id='stub', name='stub', api='completion', provider='openai', base_url='https://invalid.example'))
    monkeypatch.setattr(D, '_run_loop_blocking', lambda **_: ('PRIVATE_REPLY', {'input_tokens': 1, 'output_tokens': 1}, []))
    monkeypatch.setattr(D, '_memory_write', lambda _: None)
    set_backend(None)
    yield D, db, profile, state
    set_backend(None)
    for store in getattr(db, '_stores', {}).values():
        if hasattr(store, '_flush_index'):
            store._flush_index()
            atexit.unregister(store._flush_index)


@pytest.mark.parametrize('mode', ['off', 'read_only'])
def test_restricted_turn_is_never_written_by_idle_writer(runtime, monkeypatch, mode):
    from openprogram.state.memory import session_watcher, writing
    D, db, profile, state = runtime
    profile['memory']['mode'] = mode
    monkeypatch.setattr(writing, '_agent', lambda *_: object())
    monkeypatch.setattr(writing, '_counter', lambda: len)
    monkeypatch.setattr(writing, '_run_agent', lambda *_, **__: [{'tool': 'agent'}])
    from openprogram.runtime.permissions.authority import local_owner_authority
    result = D.process_user_turn(agent_turn_request.TurnRequest(session_id='s', user_text='PRIVATE_INPUT', agent_id='private', source='tui', **local_owner_authority()))
    assert not result.failed, result.error
    session_watcher._process_session('s', db.get_branch('s'))
    assert not any(m.get(writing.WRITTEN_NODE_MARKER) for m in db.get_branch('s'))


def _turn(runtime, **kwargs):
    from openprogram.runtime.permissions.authority import local_owner_authority
    D, _, _, _ = runtime
    req = agent_turn_request.TurnRequest(session_id=kwargs.pop('session_id', 's'), user_text='PRIVATE_INPUT', agent_id='private', source='tui', **local_owner_authority(), **kwargs)
    return req, D.process_user_turn(req)


def test_read_write_writes_only_its_own_space(runtime, monkeypatch):
    from openprogram.state.memory import session_watcher, writing
    _, db, profile, state = runtime
    profile['memory']['mode'] = 'read_write'
    monkeypatch.setattr(writing, '_agent', lambda *_: object())
    monkeypatch.setattr(writing, '_counter', lambda: len)
    monkeypatch.setattr(writing, '_run_agent', lambda *_, **__: [{'tool': 'agent'}])
    req, result = _turn(runtime)
    assert not result.failed, result.error
    session_watcher._process_session('s', db.get_branch('s'))
    from openprogram.state.memory.workspace_layout import runtime_dir
    root = state / 'memory-spaces' / 'agents' / 'private'
    space = (runtime_dir(root) / 'workspace-id').read_text().strip()
    marks = {m.get(writing.WRITTEN_NODE_MARKER) for m in db.get_branch('s')
             if m.get('role') in ('user', 'assistant')}
    assert marks == {space}
    assert req.memory_policy_snapshot['eligible'] is True


def test_disable_reenable_does_not_backfill_old_nodes(runtime, monkeypatch):
    from openprogram.state.memory import session_watcher, writing
    _, db, profile, state = runtime
    profile['memory']['mode'] = 'read_write'
    _turn(runtime)
    profile['memory']['mode'] = 'off'
    profile['memory_policy_epoch'] = 2
    profile['memory']['mode'] = 'read_write'
    profile['memory_policy_epoch'] = 3
    monkeypatch.setattr(writing, '_agent', lambda *_: pytest.fail('revoked nodes reached the model'))
    session_watcher._process_session('s', db.get_branch('s'))
    assert not list(state.rglob('sources/**/*.md'))
    req, result = _turn(runtime)
    assert not result.failed, result.error
    assert req.memory_policy_snapshot['eligible'] is False


def test_trial_override_survives_idle_and_cannot_widen_saved_off(runtime):
    from openprogram.state.memory import session_watcher
    _, db, profile, state = runtime
    draft = {**profile, 'memory': {**profile['memory'], 'mode': 'read_write'}}
    req, result = _turn(runtime, profile_snapshot=draft, memory_policy_override={'mode': 'read_only'})
    assert not result.failed, result.error
    assert req.memory_policy_snapshot['mode'] == 'off'
    assert req.memory_policy_snapshot['eligible'] is False
    session_watcher._process_session('s', db.get_branch('s'))
    assert not list(state.rglob('sources/**/*.md'))


def test_required_backend_failure_stops_before_model_and_optional_reports(runtime, monkeypatch):
    D, _, profile, _ = runtime
    profile['memory'].update(mode='read_only', required=True)
    backend = SimpleNamespace(name='local', is_available=lambda: False)
    monkeypatch.setattr('openprogram.state.memory.get_backend', lambda: backend)
    monkeypatch.setattr(D, '_run_loop_blocking', lambda **_: pytest.fail('required memory failure invoked a model'))
    _, result = _turn(runtime)
    assert result.failed and result.error_reason == 'unknown'
    assert result.error_kind == 'memory_unavailable'
    profile['memory']['required'] = False
    monkeypatch.setattr(D, '_run_loop_blocking', lambda **_: ('ok', {}, []))
    req, result = _turn(runtime, session_id='optional')
    assert not result.failed, result.error
    assert req.memory_degraded_reason == 'MEMORY_UNAVAILABLE'


def test_direct_tools_and_core_obey_mode_and_space(runtime):
    from openprogram.state.memory import get_backend
    from openprogram.state.memory.policy import resolve, scope, space_path
    from openprogram.programs.tools.knowledge.memory.memory import memory_get, memory_update
    import json
    _, _, profile, state = runtime
    profile['memory']['mode'] = 'read_only'
    policy = resolve('private', profile)
    root = space_path(policy, 'self')
    root.mkdir(parents=True)
    (root / 'core.md').write_text('# Core\nSELF_ONLY')
    (state / 'memory').mkdir()
    (state / 'memory' / 'core.md').write_text('# Core\nGLOBAL_SECRET')
    with scope(policy):
        assert 'SELF_ONLY' in get_backend().system_prompt()
        assert 'GLOBAL_SECRET' not in get_backend().system_prompt()
        assert json.loads(memory_update(base_revision='unused'))['error']['code'] == 'MEMORY_ACCESS_DENIED'
        denied = json.loads(memory_get(path='core.md', space='legacy_global'))['error']
        assert denied['code'] == 'MEMORY_ACCESS_DENIED'
        # The denial names the authorized space so the model can retry.
        assert "'legacy_global' is not authorized" in denied['message']
        assert 'authorized: self' in denied['message'] and 'Omit `space`' in denied['message']
    profile['memory']['mode'] = 'off'
    with scope(resolve('private', profile)):
        assert get_backend().system_prompt() == ''
        assert get_backend().search('SELF_ONLY') == ''
        assert json.loads(memory_get(path='core.md'))['error']['code'] == 'MEMORY_ACCESS_DENIED'


def test_commit_rechecks_epoch_and_agent_lifetime(runtime):
    from openprogram.state.memory.policy import resolve, scope, commit_guard, space_path, MemoryPolicyError
    _, _, profile, _ = runtime
    profile['memory']['mode'] = 'read_write'
    profile['created_at'] = 1
    policy = resolve('private', profile)
    with scope(policy):
        with commit_guard(space_path(policy, 'self')):
            pass
        profile['memory_policy_epoch'] += 1
        with pytest.raises(MemoryPolicyError):
            with commit_guard(space_path(policy, 'self')):
                pytest.fail('revoked commit admitted')
        profile['memory_policy_epoch'] = policy.epoch
        profile['created_at'] = 2
        with pytest.raises(MemoryPolicyError):
            with commit_guard(space_path(policy, 'self')):
                pytest.fail('replacement Agent admitted')


def test_summary_preserves_restricted_source_eligibility(runtime):
    from openprogram.runtime.context.persistence import Persister
    from openprogram.state.memory.policy import node_policy
    _, db, _, _ = runtime
    _turn(runtime)
    history = db.get_branch('s')
    summary = Persister().insert_summary_node('s', summary_text='PRIVATE_INPUT', cut_idx=len(history)-1, history=history)
    assert summary
    row = next(row for row in db.get_messages('s') if row['id'] == summary)
    assert node_policy(row)['eligible'] is False


def test_concurrent_policy_scopes_restore_after_cancellation(runtime):
    import asyncio
    from dataclasses import replace
    from openprogram.state.memory.policy import resolve, scope, current, selected_root
    _, _, profile, _ = runtime
    profile['memory']['mode'] = 'read_only'
    original = resolve('private', profile)
    async def task(space):
        policy = replace(original, read_spaces=(space,))
        with scope(policy, space=space):
            await asyncio.sleep(0)
            assert current() == policy
            assert selected_root().name == ('private' if space == 'self' else 'memory')
            if space == 'self':
                raise asyncio.CancelledError
    async def run():
        return await asyncio.gather(task('self'), task('legacy_global'), return_exceptions=True)
    profile['memory']['read_spaces'].append('legacy_global')
    result = asyncio.run(run())
    assert isinstance(result[0], asyncio.CancelledError)
    assert result[1] is None
    assert current() is None


@pytest.mark.parametrize('reader', ['conversation', 'job'])
def test_consumed_protected_output_restricts_live_tools_and_persisted_reply(runtime, monkeypatch, reader):
    from openprogram.programs.tools.knowledge.memory.memory import memory_update
    from openprogram.state.memory.policy import node_policy, current
    import json
    D, db, profile, _ = runtime
    source_req, _ = _turn(runtime, session_id='source')
    profile['memory']['mode'] = 'read_write'
    profile['memory_policy_epoch'] += 1
    def run(**kwargs):
        assert current().eligible
        if reader == 'conversation':
            from openprogram.programs.tools.knowledge.read_conversation import read_conversation
            import asyncio
            output = asyncio.run(read_conversation.execute('read-history', {'session_id': 'source'}, None, None))
            assert 'PRIVATE_REPLY' in output.content[0].text
        else:
            from openprogram.programs.tools.agents.agent.job_output.job_output import _job_output_impl
            from openprogram.runtime import jobs as job
            monkeypatch.setattr('openprogram.programs.tools.agents.agent._ownership.check_job_ownership', lambda *_: None)
            task = SimpleNamespace(id='j1', status=SimpleNamespace(value='completed'), result_text='PRIVATE_REPLY', parent_session_id='source', head_id=source_req.user_msg_id + '_reply')
            monkeypatch.setattr(job, 'get_runner', lambda: SimpleNamespace(await_job=lambda *_, **__: task, get_job_resource_view=lambda _: None))
            assert 'PRIVATE_REPLY' in _job_output_impl('j1').content[0].text
        assert not current().eligible
        assert json.loads(memory_update(base_revision='unused'))['error']['code'] == 'MEMORY_ACCESS_DENIED'
        return 'derived PRIVATE_REPLY', {}, []
    monkeypatch.setattr(D, '_run_loop_blocking', run)
    req, result = _turn(runtime, session_id='consumer')
    assert not result.failed, result.error
    reply = next(row for row in db.get_branch('consumer') if row['id'] == req.user_msg_id + '_reply')
    assert node_policy(reply)['eligible'] is False


def test_resumed_trial_session_keeps_restriction_without_request_override(runtime):
    _, db, profile, state = runtime
    profile['memory']['mode'] = 'read_write'
    db.create_session('trial', 'private')
    db.update_session('trial', agent_profile_snapshot=profile, agent_trial=True)
    req, result = _turn(runtime, session_id='trial')
    assert not result.failed, result.error
    assert req.memory_policy_snapshot['mode'] == 'read_only'
    assert req.memory_policy_snapshot['eligible'] is False


def test_off_never_calls_the_backend_reader(runtime, monkeypatch):
    import openprogram.state.memory as memory
    backend = SimpleNamespace(name='local', supports_execution_policy=True,
        is_available=lambda: True,
        search=lambda *_a, **_k: pytest.fail('off called a reader'),
        system_prompt=lambda **_k: pytest.fail('off called a reader'))
    monkeypatch.setattr(memory, '_backend', backend)
    _, result = _turn(runtime)
    assert not result.failed, result.error


def test_restart_initial_preserves_protected_eligibility(runtime, monkeypatch):
    from openprogram.state.memory.policy import current
    from openprogram.state.memory import session_watcher, writing
    D, db, profile, state = runtime
    original, result = _turn(runtime)
    assert not result.failed
    profile['memory']['mode'] = 'read_write'
    profile['memory_policy_epoch'] += 1
    observations = []
    def resume(**kwargs):
        observations.append(current())
        return 'PRIVATE_REPLY_RESUMED', {}, []
    monkeypatch.setattr(D, '_run_loop_blocking', resume)
    result = D.process_user_turn(original, execution_context={'restart_initial': True})
    assert not result.failed, result.error
    next(row for row in db.get_messages('s') if row['id'] == original.user_msg_id + '_reply')
    monkeypatch.setattr(writing, '_agent', lambda *_: object())
    monkeypatch.setattr(writing, '_counter', lambda: len)
    monkeypatch.setattr(writing, '_run_agent', lambda *_, **__: [])
    session_watcher._process_session('s', db.get_branch('s'))
    archives = '\n'.join(path.read_text() for path in state.rglob('sources/**/*.md'))
    assert 'PRIVATE_REPLY_RESUMED' not in archives


def test_continuation_installs_memory_policy(runtime, monkeypatch):
    from openprogram.state.memory.policy import current
    from openprogram.programs.tools.knowledge.memory.memory import memory_get
    D, db, profile, state = runtime
    req, result = _turn(runtime)
    assert not result.failed
    global_root = state / 'memory'
    global_root.mkdir(parents=True, exist_ok=True)
    (global_root / 'core.md').write_text('# Core\nGLOBAL_SECRET')
    continuation = SimpleNamespace(request=req, user_message_id=req.user_msg_id, assistant_message_id=req.user_msg_id + '_reply')
    observations = []
    def resume(**kwargs):
        observations.append((current(), memory_get(path='core.md')))
        return 'done', {}, []
    monkeypatch.setattr(D, '_run_loop_blocking', resume)
    result = D.process_agent_continuation(continuation)
    assert not result.failed, result.error
    assert 'GLOBAL_SECRET' not in observations[0][1]


@pytest.mark.parametrize('restore_request_snapshot', [True, False])
def test_continuation_preserves_consumed_placeholder_restriction(runtime, monkeypatch, restore_request_snapshot):
    from openprogram.state.memory.policy import current, node_policy
    from openprogram.programs.tools.knowledge.memory.memory import memory_update
    D, db, profile, _ = runtime
    profile['memory']['mode'] = 'read_write'
    req, result = _turn(runtime)
    assert not result.failed
    reply_id = req.user_msg_id + '_reply'
    db.merge_node_metadata('s', reply_id, {'memory_policy': {**req.memory_policy_snapshot, 'eligible': False}})
    if not restore_request_snapshot:
        req.memory_policy_snapshot = None
    continuation = SimpleNamespace(request=req, user_message_id=req.user_msg_id, assistant_message_id=reply_id)
    observations = []
    def resume(**kwargs):
        observations.append((current(), memory_update(base_revision='unused')))
        return 'PROTECTED_DERIVED_REPLY', {}, []
    monkeypatch.setattr(D, '_run_loop_blocking', resume)
    result = D.process_agent_continuation(continuation)
    assert not result.failed, result.error
    assert observations[0][0].eligible is False
    assert 'MEMORY_ACCESS_DENIED' in observations[0][1]
    reply = next(row for row in db.get_messages('s') if row['id'] == reply_id)
    assert node_policy(reply)['eligible'] is False
    assert current() is None


def test_readiness_does_not_create_or_read_memory(runtime, monkeypatch):
    from openprogram.state.memory.policy import readiness
    _, _, profile, state = runtime
    profile['memory']['mode'] = 'read_only'
    backend = SimpleNamespace(is_available=lambda: True, supports_execution_policy=True,
                              system_prompt=lambda: pytest.fail('readiness retrieved memory'))
    monkeypatch.setattr('openprogram.state.memory.get_backend', lambda: backend)
    assert readiness('private', profile) == {'available': True, 'blocked': False, 'reason_code': None}
    assert not state.exists()


@pytest.mark.parametrize('required', [True, False])
@pytest.mark.parametrize('read_method', ['system_prompt', 'search'])
def test_memory_prompt_read_failure_stops_required_before_provider(runtime, monkeypatch, required, read_method):
    from openprogram.runtime.conversation.turn_runtime.loop_runner import run_loop_blocking
    from openprogram.state.memory import set_backend
    from openprogram.models.providers.types import AssistantMessage, EventDone, TextContent
    D, db, profile, _ = runtime
    profile['memory'].update(mode='read_only', required=required)
    profile['tools'] = {'mode': 'none'}
    calls = []
    events = []
    def broken_read(*_args, **_kwargs):
        calls.append(read_method)
        raise OSError('synthetic memory read failure')
    backend = SimpleNamespace(name='local', supports_execution_policy=True,
                              is_available=lambda: True, search=lambda *a, **kw: '',
                              system_prompt=lambda: '')
    setattr(backend, read_method, broken_read)
    set_backend(backend)
    monkeypatch.setattr('openprogram.runtime.events.emit_safe', lambda *args, **kwargs: events.append(args))
    async def provider(model, context, options):
        calls.append('provider')
        yield EventDone(reason='stop', message=AssistantMessage(
            content=[TextContent(text='reply')], api=model.api,
            provider=model.provider, model=model.id, stop_reason='stop', timestamp=1,
        ))
    monkeypatch.setattr(D, '_run_loop_blocking', lambda **kwargs: run_loop_blocking(**kwargs, stream_fn=provider))
    req, result = _turn(runtime)
    assert read_method in calls
    if required:
        assert result.failed
        assert 'Required memory access failed' in result.error
        assert result.error_reason == 'unknown'
        assert result.error_kind == 'memory_unavailable'
        assert result.error_retryable is False
        assert 'provider' not in calls
        assert db.get_session('s').get('status') != 'running'
        if read_method == 'system_prompt':
            assert db.message_exists('s', req.user_msg_id + '_reply')
    else:
        assert not result.failed, result.error
        assert calls.count('provider') == 1
        assert req.memory_degraded_reason == 'MEMORY_UNAVAILABLE'
        assert any(event[0] == 'memory.degraded' and len(event) > 3 and event[3] == {'session': 's'} for event in events)


def _post_report(db, monkeypatch):
    from openprogram.runtime.conversation.always_on import runtime as always_on
    monkeypatch.setattr(always_on, '_broadcast', lambda *_: None)
    return always_on.post_report_message('s', 'private', {'id': 'r1', 'title': 'Found it', 'body': 'REPORT_BODY'})


def _writable(monkeypatch, *, counter=len):
    from openprogram.state.memory import writing
    monkeypatch.setattr(writing, '_agent', lambda *_: object())
    monkeypatch.setattr(writing, '_counter', lambda: counter)
    monkeypatch.setattr(writing, '_run_agent', lambda *_, **__: [{'tool': 'agent'}])
    return writing


def test_owner_turn_after_a_report_still_writes_memory(runtime, monkeypatch):
    from openprogram.state.memory import session_watcher
    from openprogram.state.memory.policy import eligible
    _, db, profile, state = runtime
    profile['memory']['mode'] = 'read_write'
    writing = _writable(monkeypatch)
    _turn(runtime)
    report_id = _post_report(db, monkeypatch)
    report = next(row for row in db.get_branch('s') if row['id'] == report_id)
    assert eligible(report)

    req, result = _turn(runtime)

    assert not result.failed, result.error
    assert req.memory_policy_snapshot['eligible'] is True
    session_watcher._process_session('s', db.get_branch('s'))
    from openprogram.state.memory.workspace_layout import runtime_dir
    space = (runtime_dir(state / 'memory-spaces' / 'agents' / 'private') / 'workspace-id').read_text().strip()
    rows = {row['id']: row for row in db.get_branch('s') if row.get('role') in ('user', 'assistant')}
    assert rows[report_id].get(writing.WRITTEN_NODE_MARKER) is None
    assert all(
        row.get(writing.WRITTEN_NODE_MARKER) == space for key, row in rows.items() if key != report_id
    )
    assert not (state / 'memory').exists() or not list((state / 'memory').rglob('*.md'))


def test_other_policyless_rows_of_a_self_space_agent_stay_ineligible(runtime):
    from openprogram.state.memory.policy import eligible
    _, _, profile, _ = runtime
    profile['memory']['mode'] = 'read_write'
    assert not eligible({'id': 'old', 'role': 'assistant', 'agent_id': 'private', 'content': 'x'})


def test_already_stamped_conversation_stays_ineligible_after_a_report(runtime, monkeypatch):
    _, db, profile, _ = runtime
    profile['memory']['mode'] = 'read_write'
    first, _ = _turn(runtime)
    db.merge_node_metadata('s', first.user_msg_id, {'memory_policy': {**first.memory_policy_snapshot, 'eligible': False}})
    _post_report(db, monkeypatch)
    req, result = _turn(runtime)
    assert not result.failed, result.error
    assert req.memory_policy_snapshot['eligible'] is False


@pytest.mark.parametrize('cut_off', [False, True])
def test_idle_pass_skips_a_scheduled_branch_over_threshold(runtime, monkeypatch, cut_off):
    from openprogram.state.memory import session_watcher
    _, db, profile, _ = runtime
    profile['memory']['mode'] = 'read_write'
    writing = _writable(monkeypatch, counter=lambda _text: 10**6)
    req, _ = _turn(runtime)
    head = db.get_session('s')['head_id']
    policy = req.memory_policy_snapshot
    db.append_message('s', {'id': 'task_user', 'role': 'user', 'content': 'SCHEDULED', 'predecessor': head,
                            'source': 'scheduler', 'timestamp': 2.0, 'memory_policy': policy})
    if not cut_off:
        db.append_message('s', {'id': 'task_user_reply', 'role': 'assistant', 'content': 'RAN', 'predecessor': 'task_user',
                                'source': 'scheduler', 'timestamp': 3.0, 'memory_policy': policy})
    db.set_head('s', head)
    tip = 'task_user' if cut_off else 'task_user_reply'
    assert writing._scheduled_branch(db.get_branch('s', tip))

    session_watcher._process_session('s', db.get_branch('s'))

    rows = {row['id']: row for row in db.get_messages('s')}
    assert rows['task_user'].get(writing.WRITTEN_NODE_MARKER) is None
    assert rows.get(tip, {}).get(writing.WRITTEN_NODE_MARKER) is None
    assert rows[head].get(writing.WRITTEN_NODE_MARKER)


def test_branch_the_owner_replied_on_is_not_skipped(monkeypatch):
    from openprogram.state.memory import writing
    assert not writing._scheduled_branch([
        {'role': 'user', 'source': 'scheduler'}, {'role': 'assistant'}, {'role': 'user', 'source': 'web'},
    ])


def _scheduled_turn(runtime, **kwargs):
    from openprogram.runtime.permissions.authority import local_owner_authority
    D, db, _, _ = runtime
    authority = {**local_owner_authority(), 'speaker_kind': 'runtime', 'speaker_id': 'runtime/scheduler', 'speaker_display': 'scheduler'}
    req = agent_turn_request.TurnRequest(session_id='s', user_text='SCHEDULED', agent_id='private', source='scheduler', advance_head=False,
                                         branch_from=db.get_session('s')['head_id'], **authority, **kwargs)
    result = D.process_user_turn(req)
    assert not result.failed, result.error
    return req, result


def test_idle_pass_skips_a_real_scheduled_turn(runtime, monkeypatch):
    from openprogram.state.memory import session_watcher
    _, db, profile, _ = runtime
    profile['memory']['mode'] = 'read_write'
    writing = _writable(monkeypatch, counter=lambda _text: 10**6)
    _turn(runtime)
    head = db.get_session('s')['head_id']
    req, result = _scheduled_turn(runtime)
    assert db.get_session('s')['head_id'] == head
    assert writing._scheduled_branch(db.get_branch('s', result.assistant_msg_id))

    session_watcher._process_session('s', db.get_branch('s'))

    rows = {row['id']: row for row in db.get_messages('s')}
    assert rows[req.user_msg_id].get(writing.WRITTEN_NODE_MARKER) is None
    assert rows[result.assistant_msg_id].get(writing.WRITTEN_NODE_MARKER) is None
    assert rows[head].get(writing.WRITTEN_NODE_MARKER)


def test_per_turn_pass_skips_a_checked_out_scheduled_branch(runtime, monkeypatch):
    _, db, profile, _ = runtime
    profile['memory']['mode'] = 'read_write'
    writing = _writable(monkeypatch, counter=lambda _text: 10**6)
    _turn(runtime)
    req, result = _scheduled_turn(runtime)
    db.set_head('s', result.assistant_msg_id)

    assert writing.write('s', token_threshold=1) is None

    assert not any(row.get(writing.WRITTEN_NODE_MARKER) for row in db.get_messages('s'))


def test_report_is_not_a_source_for_an_agent_writing_to_the_shared_space(runtime, monkeypatch):
    from openprogram.state.memory import session_watcher
    _, db, profile, _ = runtime
    profile['memory'] = {'mode': 'read_write', 'read_spaces': ['legacy_global'], 'write_space': 'legacy_global', 'required': False}
    profile['memory_policy_epoch'] = 0
    writing = _writable(monkeypatch)
    _turn(runtime)
    report_id = _post_report(db, monkeypatch)
    _turn(runtime)

    session_watcher._process_session('s', db.get_branch('s'))

    rows = {row['id']: row for row in db.get_branch('s') if row.get('role') in ('user', 'assistant')}
    assert rows[report_id].get(writing.WRITTEN_NODE_MARKER) is None
    assert all(row.get(writing.WRITTEN_NODE_MARKER) for key, row in rows.items() if key != report_id)
