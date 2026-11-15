"""Agent ownership is distinct from Python helper call depth."""
import asyncio

import pytest

from openprogram import Agent
from openprogram.runtime.context.scope import _call_id
from openprogram.state.store.session.nodes import Call, ROLE_USER
from openprogram.state.store import SessionNodeWriter, SessionStore, _store


@pytest.fixture
def chat(tmp_path):
    store = SessionStore(tmp_path / 'sessions')
    store.create_session('chat', 'main')
    writer = SessionNodeWriter(store, 'chat')
    writer.append(Call(id='request', role=ROLE_USER, output='private parent history'))
    token = _store.set(writer)
    caller = _call_id.set('request')
    try:
        yield writer
    finally:
        _call_id.reset(caller)
        _store.reset(token)
        store.close()


@pytest.mark.parametrize('asynchronous', [False, True])
def test_different_agent_has_independent_persistent_graph(chat, asynchronous):
    observations = []
    class Child(Agent):
        def compute(self):
            writer = _store.get()
            observations.append((writer.session_id, _call_id.get()))
            return self.helper()
        def helper(self):
            return 'child result'
        async def acompute(self):
            await asyncio.sleep(0)
            return self.compute()
    class Parent(Agent):
        def run(self):
            return Child().compute()
        async def arun_child(self):
            return await Child().acompute()
    parent = Parent()
    result = asyncio.run(parent.arun_child()) if asynchronous else parent.run()
    assert result == 'child result'
    assert _store.get() is chat
    assert _call_id.get() == 'request'
    child_id, child_call = observations[0]
    assert child_id != chat.session_id
    child = SessionNodeWriter(chat.store, child_id).load()
    assert child_call in child.nodes
    assert all(not node.caller or node.caller in child.nodes for node in child.nodes.values())
    assert not any(node.name.endswith('.helper') for node in child.nodes.values())
    assert not any('Child.' in node.name for node in chat.load().nodes.values())
    assert chat.store.get_session(child_id)['parent_session_id'] != child_id


def test_child_failure_restores_parent(chat):
    class Child(Agent):
        def run(self):
            raise ValueError('child failure')
    with pytest.raises(ValueError, match='child failure'):
        Child().run()
    assert _store.get() is chat
    assert _call_id.get() == 'request'


@pytest.mark.parametrize('history', [None, 'thread'])
def test_child_preserves_explicit_context_policy_without_parent_history(chat, history):
    from openprogram import Context, Runtime
    observed = []
    def provider(content, **kwargs):
        context = Context.current()
        observed.append((context.history, context.store.session_id,
                         set(context.store.load().nodes)))
        return 'answer'
    runtime = Runtime(call=provider)
    try:
        with Context(history=history).bind():
            assert Agent(runtime=runtime, tools=[])('child request') == 'answer'
        policy, session, ids = observed[0]
        assert policy == history
        assert session != 'chat'
        assert 'request' not in ids
    finally:
        runtime.close()


@pytest.mark.parametrize('asynchronous', [False, True])
def test_plain_agent_entry_owns_child_graph(chat, asynchronous):
    from openprogram import Runtime
    from openprogram.runtime.agent import agent, agent_async
    observed = []
    def provider(content, **kwargs):
        observed.append(_store.get().session_id)
        return 'done'
    runtime = Runtime(call=provider)
    try:
        if asynchronous:
            result = asyncio.run(agent_async('child', runtime=runtime, tools=[]))
        else:
            result = agent('child', runtime=runtime, tools=[])
        assert result == 'done'
        assert observed and set(observed) != {'chat'}
        assert not any(node.is_llm() for node in chat.load().nodes.values())
        links = [node for node in chat.load().nodes.values() if node.metadata.get('child_session_id')]
        assert len(links) == 1
        assert links[0].output == 'done'
    finally:
        runtime.close()


def test_child_cancellation_restores_parent_and_cancel_signal(chat):
    from openprogram.host.cancellation import _current_cancel
    cancel = asyncio.Event()
    class Child(Agent):
        async def run(self):
            assert _current_cancel.get() is cancel
            assert _store.get() is not chat
            raise asyncio.CancelledError()
    token = _current_cancel.set(cancel)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(Child().run())
        assert _store.get() is chat
        assert _call_id.get() == 'request'
        link = next(node for node in chat.load().nodes.values() if node.metadata.get('child_session_id'))
        assert link.metadata['status'] == 'cancelled'
    finally:
        _current_cancel.reset(token)


def test_direct_tool_records_once_without_ordinary_helpers(chat):
    class Tool(Agent):
        method_options = {'run': {'tool': True, 'register_globally': False}}
        def run(self, depth: int):
            return self.helper(depth)
        def helper(self, depth):
            return 1 if not depth else 1 + self.helper(depth - 1)
    tool = Tool().run._agent_tool
    result = asyncio.run(tool.execute('tool-id', {'depth': 3}, None, None))
    assert not result.is_error
    nodes = [n for n in chat.load().nodes.values() if n.is_code()]
    assert len(nodes) == 1
    assert nodes[0].name.endswith('.run')
    assert not nodes[0].metadata.get('child_session_id')
    assert nodes[0].output == 4


def test_child_file_checkpoints_keep_parent_turn_ownership(chat):
    from openprogram.state.store import _current_turn_id
    from openprogram.state.store.snapshot.checkpoint.helpers import _locked_checkpoint
    class Child(Agent):
        def run(self):
            with _locked_checkpoint() as (_, turn, writer):
                assert writer is chat
                assert turn == 'parent-turn'
    token = _current_turn_id.set('parent-turn')
    try:
        Child().run()
    finally:
        _current_turn_id.reset(token)


@pytest.mark.parametrize('kind', [staticmethod, classmethod])
def test_class_and_static_child_entries_own_graph(chat, kind):
    class Child(Agent):
        @kind
        def run(*args):
            return _store.get().session_id
    class Parent(Agent):
        def run(self):
            parent_id = _store.get().session_id
            return parent_id, Child.run()
    parent_id, child_id = Parent().run()
    assert parent_id != child_id
    assert parent_id != 'chat'
    child = SessionNodeWriter(chat.store, child_id).load()
    assert child.nodes
    assert all(not n.caller or n.caller in child.nodes for n in child.nodes.values())


def test_child_choice_result_is_recorded_in_parent(chat):
    from openprogram import Runtime
    runtime = Runtime(call=lambda content, **kwargs: '{"call": "A"}')
    try:
        assert Agent(runtime=runtime).choose('choose', {'A': 'first', 'B': 'second'}) == 'A'
        link = next(n for n in chat.load().nodes.values() if n.metadata.get('child_session_id'))
        assert link.output == 'A'
    finally:
        runtime.close()


@pytest.mark.parametrize('entry', ['constructor', 'override', 'plain'])
def test_explicit_parent_context_preserves_content_not_graph(chat, entry):
    from openprogram import Context, Runtime
    from openprogram.runtime.agent import agent
    observed = []
    def provider(content, **kwargs):
        ctx = Context.current()
        observed.append((ctx.resolve_blocks(), _store.get().session_id,
                         set(_store.get().load().nodes)))
        return 'done'
    class Parent(Agent):
        def run(self):
            current = Context.current()
            parent_id = _store.get().session_id
            if entry == 'constructor':
                result = Agent(context=current, runtime=runtime, tools=[])('child')
            elif entry == 'override':
                result = Agent(runtime=runtime, tools=[])('child', context=current)
            else:
                result = agent('child', context=current, runtime=runtime, tools=[])
            return parent_id, result
    runtime = Runtime(call=provider)
    try:
        with Context({'facts': 'payload'}, history=None).bind():
            parent_id, result = Parent().run()
        assert result == 'done'
        blocks, child_id, ids = observed[0]
        assert blocks == {'facts': 'payload'}
        assert child_id != parent_id
        assert 'request' not in ids
    finally:
        runtime.close()


@pytest.mark.parametrize('entry', ['call', 'arun', 'choose'])
def test_tool_internal_agent_request_owns_child_graph(chat, entry):
    from openprogram import Runtime
    runtime = Runtime(call=lambda content, **kwargs: '{"call":"A"}' if entry == 'choose' else 'answer')
    class Tool(Agent):
        method_options = {'run': {'tool': True, 'register_globally': False}}
        if entry == 'arun':
            async def run(self):
                return await self.arun('child', tools=[])
        else:
            def run(self):
                if entry == 'choose':
                    return self.choose('child', {'A':'option'})
                return self('child', tools=[])
    try:
        result = asyncio.run(Tool(runtime=runtime).run._agent_tool.execute('outer-tool', {}, None, None))
        assert not result.is_error
        graph = chat.load()
        assert not any(n.is_llm() for n in graph)
        link = next(n for n in graph if n.metadata.get('child_session_id'))
        child = SessionNodeWriter(chat.store, link.metadata['child_session_id']).load()
        assert any(n.is_llm() for n in child)
        assert all(not n.caller or n.caller in child.nodes for n in child)
        assert link.output == ('A' if entry == 'choose' else 'answer')
        assert _store.get() is chat and _call_id.get() == 'request'
    finally:
        runtime.close()
