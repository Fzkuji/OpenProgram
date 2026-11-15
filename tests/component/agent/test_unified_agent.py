"""Public session execution and Context must be sufficient for chat."""
import asyncio
import threading

import pytest

from openprogram import Agent, Context
from openprogram.runtime.conversation import turn_runtime as D
from openprogram.runtime.agent import turn_request as agent_turn_request
from openprogram.state.store import SessionStore


def test_context_session_and_agent_turn(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    context = Context.for_session(store, 'one', blocks={'facts': 'value'})
    seen = []
    def execute(req, **kwargs):
        seen.append((Context.current().session_id, Context.current().resolve_blocks()))
        kwargs['on_event']({'type': 'sample'})
        return D.TurnResult('done', 'u', 'a')
    monkeypatch.setattr(D, '_process_turn_once', execute)
    events = []
    try:
        result = Agent(context=context).run_turn(agent_turn_request.TurnRequest('one', 'hello', 'main', 'python'), on_event=events.append)
        assert result.final_text == 'done'
        assert seen == [('one', {'facts': 'value'})]
        assert events == [{'type': 'sample'}]
        assert Context.current() is None
    finally:
        store.close()


def test_chat_entry_uses_agent_subclass(monkeypatch):
    from openprogram import ChatAgent
    seen = []
    def run(self, request, **kwargs):
        seen.append(type(self))
        return D.TurnResult('done', 'u', 'a')
    monkeypatch.setattr(Agent, 'run_turn', run)
    assert D.process_user_turn(agent_turn_request.TurnRequest('one', 'hello', 'main', 'web')).final_text == 'done'
    assert seen == [ChatAgent]


def test_async_turn_cancellation_signals_worker(monkeypatch):
    entered, cancelled = threading.Event(), threading.Event()
    def execute(req, **kwargs):
        entered.set()
        assert kwargs['cancel_event'].wait(30)
        cancelled.set()
        return D.TurnResult('', 'u', 'a')
    monkeypatch.setattr(D, '_process_turn_once', execute)
    async def run():
        task = asyncio.create_task(Agent().arun_turn(agent_turn_request.TurnRequest('one', 'hello', 'main', 'python')))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
    asyncio.run(run())


def test_async_turn_cancellation_fires_the_stop(monkeypatch):
    # The turn's loop is woken by the wakeups registered on its signal, not
    # by a poll: a bare ``set`` would reach a running tool only at the backstop.
    from openprogram.runtime.execution.run_control import wakeups
    entered, woken = threading.Event(), threading.Event()
    def execute(req, **kwargs):
        handle = wakeups.register(kwargs['cancel_event'], woken.set)
        try:
            entered.set()
            assert woken.wait(30)
        finally:
            wakeups.unregister(handle)
        return D.TurnResult('', 'u', 'a')
    monkeypatch.setattr(D, '_process_turn_once', execute)
    async def run():
        task = asyncio.create_task(Agent().arun_turn(agent_turn_request.TurnRequest('one', 'hello', 'main', 'python')))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert woken.is_set()
    asyncio.run(run())


def test_async_turn_under_an_anyio_cancel_scope_waits_for_its_worker(monkeypatch):
    # anyio cancels the task again at every await, so the cleanup must keep
    # waiting until the owner thread is done, not return at the first one.
    import anyio
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    def execute(req, **kwargs):
        entered.set()
        assert kwargs['cancel_event'].wait(30)
        release.wait(30)
        ended.set()
        return D.TurnResult('', 'u', 'a')
    monkeypatch.setattr(D, '_process_turn_once', execute)
    async def run():
        with anyio.CancelScope() as scope:
            async def stop_later():
                await anyio.to_thread.run_sync(entered.wait, 2)
                # The owner thread keeps writing a while after the stop.
                threading.Timer(0.2, release.set).start()
                scope.cancel()
            async with anyio.create_task_group() as group:
                group.start_soon(stop_later)
                await Agent().arun_turn(agent_turn_request.TurnRequest('one', 'hello', 'main', 'python'))
        return ended.is_set()
    assert anyio.run(run)


def test_public_agent_runs_real_loop_with_context_and_history(tmp_path, monkeypatch):
    from tests.unit.agent.dispatcher.test_dispatcher_integration import _stub_model, make_text_stream_fn
    store = SessionStore(tmp_path)
    monkeypatch.setattr(D, '_resolve_model', lambda *a, **kw: _stub_model())
    monkeypatch.setattr(D, '_load_agent_profile', lambda _: {'id': 'main', 'tools': {'mode':'none'}, 'system_prompt':'system'})
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: store)
    seen = []
    async def stream(model, context, options):
        seen.append(context)
        async for event in make_text_stream_fn(['done'])(model, context, options):
            yield event
    agent = Agent(context=Context.for_session(store, 'one', blocks={'facts':'explicit-value'}), stream_fn=stream)
    try:
        one = agent.run_turn(agent_turn_request.TurnRequest('one', 'first-message', 'main', 'python'))
        two = agent.run_turn(agent_turn_request.TurnRequest('one', 'second-message', 'main', 'python'))
        assert not one.failed and not two.failed
        assert one.final_text == two.final_text == 'done'
        assert 'explicit-value' in str(seen[0].messages)
        assert 'first-message' in str(seen[1].messages)
        assert 'explicit-value' not in seen[0].system_prompt
        agent.run_turn(agent_turn_request.TurnRequest('one','without-history','main','python'), context=Context(history=None))
        assert 'first-message' not in str(seen[-1].messages)
        assert Context.current() is None
    finally:
        store.close()


def test_context_session_mismatch_rejected_before_execution(tmp_path):
    store = SessionStore(tmp_path)
    try:
        agent = Agent(context=Context.for_session(store, 'one'))
        with pytest.raises(ValueError, match='same session'):
            agent.run_turn(agent_turn_request.TurnRequest('two', 'hello', 'main', 'python'))
        assert store.get_session('two') is None
    finally:
        store.close()


def test_tool_timeout_does_not_cancel_parent():
    parent = threading.Event()
    class Tools(Agent):
        method_options = {'slow': {'tool':True, 'timeout':0.01, 'register_globally':False}}
        async def slow(self):
            await asyncio.sleep(1)
    result = asyncio.run(Tools().slow._agent_tool.execute('timeout', {}, parent, None))
    assert result.is_error
    assert not parent.is_set()


def test_subclass_turn_override_is_not_wrapped_as_nested_function(monkeypatch):
    seen = []
    class Custom(Agent):
        instructions = None
        def run_turn(self, request, **options):
            seen.append('override')
            return super().run_turn(request, **options)
    def execute(req, **kwargs):
        from openprogram.runtime.agent.runtime_scope import _agent_graph_owner
        assert isinstance(_agent_graph_owner.get(), Custom)
        return D.TurnResult('done', 'u', 'a')
    monkeypatch.setattr(D, '_process_turn_once', execute)
    assert Custom().run_turn(agent_turn_request.TurnRequest('one', 'hello', 'main', 'python')).final_text == 'done'
    assert seen == ['override']


def test_agent_resume_preserves_frozen_request_and_subtype(monkeypatch):
    from types import SimpleNamespace
    from openprogram import ChatAgent
    request = agent_turn_request.TurnRequest('one', 'hello', 'main', 'python', profile_snapshot={'system_prompt':'frozen'})
    continuation = SimpleNamespace(request=request)
    seen = []
    def execute(value, **options):
        from openprogram.runtime.agent.turn_api import _current_turn_agent
        seen.append(type(_current_turn_agent.get()))
        assert value.request.profile_snapshot == {'system_prompt':'frozen'}
        return D.TurnResult('resumed', 'u', 'a')
    monkeypatch.setattr(D, 'execute_continuation', execute)
    result = ChatAgent(instructions='changed').resume_turn(continuation)
    assert result.final_text == 'resumed' and seen == [ChatAgent]


def test_explicit_context_model_pin_isolated_from_default_store(tmp_path, monkeypatch):
    from tests.unit.agent.dispatcher.test_dispatcher_integration import _stub_model, make_text_stream_fn
    default, explicit = SessionStore(tmp_path/'default'), SessionStore(tmp_path/'explicit')
    for store, provider, model in [(default,'wrong','default-model'), (explicit,'right','explicit-model')]:
        store.create_session('same', 'main')
        store.update_session('same', provider_override=provider, model_override=model)
    seen = []
    def resolve(profile, override=None):
        seen.append(override)
        return _stub_model()
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: default)
    monkeypatch.setattr(D, '_resolve_model', resolve)
    monkeypatch.setattr(D, '_load_agent_profile', lambda _: {'id':'main','tools':{'mode':'none'}})
    try:
        worker = Agent(context=Context.for_session(explicit,'same'), stream_fn=make_text_stream_fn(['ok']))
        result = worker.run_turn(agent_turn_request.TurnRequest('same','hello','main','python'))
        assert not result.failed, result.error
        assert seen[0] == 'right/explicit-model'
        assert not default.get_messages('same')
    finally:
        default.close()
        explicit.close()


def test_tool_timeout_marks_only_actual_tool_node(tmp_path):
    from openprogram.state.store.session.nodes import Call, ROLE_LLM
    from openprogram.state.store import SessionNodeWriter, _store
    from openprogram.runtime.context.scope import _call_id
    store = SessionStore(tmp_path)
    store.create_session('chat','main')
    writer=SessionNodeWriter(store,'chat')
    writer.append(Call(id='parent',role=ROLE_LLM,metadata={'status':'running'}))
    st, ct = _store.set(writer), _call_id.set('parent')
    parent=threading.Event()
    class Slow(Agent):
        method_options={'slow':{'tool':True,'timeout':0.01,'register_globally':False}}
        async def slow(self):
            await asyncio.sleep(1)
    try:
        result=asyncio.run(Slow().slow._agent_tool.execute('slow-call',{},parent,None))
        assert result.is_error and not parent.is_set()
        graph=writer.load()
        assert graph.nodes['parent'].metadata['status']=='running'
        children=[node for node in graph if node.id!='parent']
        assert len(children)==1
        assert children[0].metadata['status']=='error'
        assert 'timed out' in str(children[0].output)
    finally:
        _call_id.reset(ct)
        _store.reset(st)
        store.close()


def test_context_store_is_used_by_shared_session_metadata(tmp_path):
    from openprogram.runtime.context.session_db import default_db
    store = SessionStore(tmp_path)
    try:
        with Context.for_session(store, 'explicit').bind():
            assert default_db() is store
    finally:
        store.close()


def test_real_chat_continues_after_tool_timeout(tmp_path, monkeypatch):
    from tests.unit.agent.dispatcher.test_dispatcher_integration import _stub_model
    from tests.component.providers.scripted_provider import ScriptedProvider, ScriptedToolCall, ScriptedText
    from openprogram import ChatAgent
    class Tools(Agent):
        method_options={'slow':{'tool':True,'timeout':0.01,'register_globally':False}}
        async def slow(self):
            await asyncio.sleep(1)
    tool=Tools().slow._agent_tool
    provider=ScriptedProvider()
    provider.add_response(ScriptedToolCall(tool.name,{},'slow-call'))
    provider.add_response(ScriptedText('continued after timeout'))
    monkeypatch.setattr(D,'_resolve_model',lambda *a,**k:_stub_model())
    monkeypatch.setattr(D,'_load_agent_profile',lambda _: {'id':'main','tools':{'mode':'none'}})
    monkeypatch.setattr(D.loop_runner,'_resolve_tools',lambda *a,**k:[tool])
    store=SessionStore(tmp_path)
    parent=threading.Event()
    try:
        worker=ChatAgent(context=Context.for_session(store,'chat'),stream_fn=provider.stream_simple)
        result=worker.run_turn(agent_turn_request.TurnRequest('chat','use the tool','main','python'),cancel_event=parent)
        assert not result.failed, result.error
        assert result.final_text=='continued after timeout'
        assert provider.call_count==2 and not parent.is_set()
        assert any(message.role=='toolResult' and message.is_error for message in provider.calls[1].context.messages)
    finally:
        store.close()
