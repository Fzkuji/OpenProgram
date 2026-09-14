"""One user message can compact completed tool results before the next call."""
import asyncio

import pytest

from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import AgentContext, AgentLoopConfig, AgentTool, AgentToolResult
from openprogram.context.tokens import estimate_history_tokens
from openprogram.providers.types import AssistantMessage, EventDone, Model, TextContent, ToolCall, UserMessage


@pytest.mark.parametrize('requested,expected', [(None, 250), (100, 100), (900, None)])
def test_small_window_default_matches_provider_output_limit(requested, expected):
    model = Model(id='small', name='small', api='openai-completions', provider='openai',
                  base_url='https://example.invalid', context_window=1000, max_tokens=8192)
    seen = []
    async def stream(_model, context, options):
        seen.append(options.max_tokens)
        result = AssistantMessage(content=[TextContent(text='done')], api=model.api,
                                  provider=model.provider, model=model.id, timestamp=0)
        yield EventDone(reason='stop', message=result)
    async def run():
        events = agent_loop([UserMessage(content='hello', timestamp=0)],
            AgentContext(tools=[], memory_prefetch=''),
            AgentLoopConfig(model=model, max_tokens=requested, convert_to_llm=lambda messages: messages),
            stream_fn=stream)
        async for _ in events:
            pass
        return await events.result()
    if expected is None:
        with pytest.raises(ValueError, match='Protected request'):
            asyncio.run(run())
        assert not seen
    else:
        asyncio.run(run())
        assert seen == [expected]


def test_one_user_message_multiple_tool_rounds(monkeypatch):
    model = Model(id='fixture', name='fixture', api='openai-completions', provider='openai',
                  base_url='https://example.invalid', context_window=12000, max_tokens=1000)
    payload = '\n'.join(f'item {i}: check failed at position {i * 17}' for i in range(3500))
    requests = []
    summaries = []

    def answer(content, stop='stop'):
        return AssistantMessage(content=content, api=model.api, provider=model.provider,
                                model=model.id, stop_reason=stop, timestamp=0)

    async def summary(_model, context, options):
        summaries.append(context)
        return answer([TextContent(text='Checks failed; inspect reported positions before making changes.')])

    monkeypatch.setattr('openprogram.providers.complete_simple', summary)

    async def execute(*_args):
        return AgentToolResult(content=[TextContent(text=payload)])

    async def stream(_model, context, options=None):
        requests.append(context.model_copy(deep=True))
        i = len(requests)
        result = answer([ToolCall(id=f'c{i}', name='check', arguments={})], 'toolUse') if i < 4 else answer([TextContent(text='analysis done')])
        yield EventDone(reason=result.stop_reason, message=result)

    async def run():
        events = agent_loop([UserMessage(content='Only analyze. Ask before editing.', timestamp=0)],
            AgentContext(tools=[AgentTool(name='check', label='check', description='check',
                parameters={'type': 'object'}, execute=execute)], memory_prefetch=''),
            AgentLoopConfig(model=model, convert_to_llm=lambda messages: messages, max_tokens=1000),
            stream_fn=stream)
        async for _ in events:
            pass
        return await events.result()

    raw = asyncio.run(run())
    assert summaries, 'same-message tool growth must trigger request compaction'
    assert len(requests) == 4
    assert all(estimate_history_tokens(r.messages) < 11000 for r in requests)
    assert all(r.messages[0].content == 'Only analyze. Ask before editing.' for r in requests)
    assert sum(m.role == 'toolResult' and m.content[0].text == payload for m in raw) == 3
    for request in requests:
        calls = {b.id for m in request.messages if m.role == 'assistant' for b in m.content if isinstance(b, ToolCall)}
        assert {m.tool_call_id for m in request.messages if m.role == 'toolResult'} == calls


def test_complete_groups_cache_and_original_evidence(monkeypatch):
    from openprogram.context.request_compaction import RequestCompactor
    from openprogram.providers.types import Context, SimpleStreamOptions, ToolResultMessage
    model = Model(id='fixture', name='fixture', api='openai-completions', provider='openai',
                  base_url='https://example.invalid', context_window=6000, max_tokens=512)
    calls = []
    credential_keys = []
    async def summary(_model, context, options):
        assert estimate_history_tokens(context.messages) + options.max_tokens < model.context_window
        calls.append(context)
        credential_keys.append(options.api_key)
        return AssistantMessage(content=[TextContent(text='Evidence remains unverified.')],
            api=model.api, provider=model.provider, model=model.id, timestamp=0)
    monkeypatch.setattr('openprogram.providers.complete_simple', summary)
    owner = AssistantMessage(content=[ToolCall(id='a', name='check', arguments={}),
                                     ToolCall(id='b', name='check', arguments={})],
        api=model.api, provider=model.provider, model=model.id, timestamp=0)
    result = ToolResultMessage(tool_call_id='a', tool_name='check', is_error=True,
        content=[TextContent(text='evidence 1234567 ' * 7000)], timestamp=0)
    other = result.model_copy(update={'tool_call_id': 'b'})
    user = UserMessage(content='Do not edit.', timestamp=0)
    raw = Context(messages=[user, owner, result, other])
    compactor = RequestCompactor()
    async def run():
        opts = SimpleStreamOptions(max_tokens=512)
        async def get_key(provider):
            assert provider == 'openai'
            return 'fixture-callback-key'
        first = await compactor.prepare(raw, model, opts, get_api_key=get_key)
        assert credential_keys and set(credential_keys) == {'fixture-callback-key'}
        count = len(calls)
        second = await compactor.prepare(raw, model, opts)
        assert len(calls) == count
        assert second.messages == first.messages
        assert first.messages[0] == user and first.messages[1] == owner
        assert all(m.is_error for m in first.messages[2:])
        assert raw.messages[2] == result
        # A partial tool group must not be summarized, even under pressure.
        import pytest
        with pytest.raises(ValueError, match='Protected request'):
            await compactor.prepare(Context(messages=[user, owner, result]), model, opts)
        assert len(calls) == count
        changed = result.model_copy(update={'content': [TextContent(text='updated evidence ' * 7000)]})
        await compactor.prepare(Context(messages=[user, owner, changed, other]), model, opts)
        assert len(calls) > count
    asyncio.run(run())


def test_function_runtime_compacts_its_tool_continuation(monkeypatch):
    from openprogram.agentic_programming import Runtime, agentic_function
    model = Model(id='fixture', name='fixture', api='openai-completions', provider='openai',
                  base_url='https://example.invalid', context_window=12000, max_tokens=1000)
    requests, executed, summaries = [], [], []
    def answer(content, stop='stop'):
        return AssistantMessage(content=content, api=model.api, provider=model.provider,
                                model=model.id, stop_reason=stop, timestamp=0)
    async def summary(_model, context, options):
        summaries.append(context)
        return answer([TextContent(text='Analysis evidence; no edits performed.')])
    monkeypatch.setattr('openprogram.providers.complete_simple', summary)
    async def execute(*_args):
        executed.append(True)
        return AgentToolResult(content=[TextContent(text='record evidence 1234567 ' * 12000)])
    async def stream(_model, context, options=None):
        requests.append(context.model_copy(deep=True))
        result = answer([ToolCall(id='inner-call', name='check', arguments={})], 'toolUse') if len(requests) == 1 else answer([TextContent(text='done')])
        yield EventDone(reason=result.stop_reason, message=result)
    runtime = Runtime(model='default')
    runtime.api_model = model
    @agentic_function
    def inspect(runtime=None):
        return runtime.exec('Analyze only; retain evidence.', stream_fn=stream,
            tools=[AgentTool(name='check', label='check', description='check',
                parameters={'type': 'object'}, execute=execute)])
    try:
        assert inspect(runtime=runtime) == 'done'
    finally:
        runtime.close()
    assert len(executed) == 1 and len(requests) == 2 and summaries
    assert estimate_history_tokens(requests[-1].messages) < 11000
    assert any(m.role == 'toolResult' and 'Tool evidence summary' in m.content[0].text
               for m in requests[-1].messages)


def test_native_schema_is_part_of_fixed_input_budget():
    import pytest
    from openprogram.context.request_compaction import RequestCompactor
    from openprogram.providers.types import Context, SimpleStreamOptions, JsonSchemaOutput
    model = Model(id='fixture', name='fixture', api='openai-completions', provider='openai',
                  base_url='https://example.invalid', context_window=6000, max_tokens=512)
    context = Context(messages=[UserMessage(content='Analyze.', timestamp=0)])
    schema = {'type': 'object', 'description': ' '.join(f'evidence_{i}' for i in range(6000))}
    options = SimpleStreamOptions(max_tokens=512, response_format=JsonSchemaOutput(schema=schema))
    with pytest.raises(ValueError, match='Protected request'):
        asyncio.run(RequestCompactor().prepare(context, model, options))


@pytest.mark.parametrize("mode", ["raised", "aborted", "signal"])
def test_cancelled_summary_never_changes_raw_messages(monkeypatch, mode):
    import pytest
    from openprogram.context.request_compaction import RequestCompactor
    from openprogram.providers.types import Context, SimpleStreamOptions, ToolResultMessage
    model = Model(id='fixture', name='fixture', api='openai-completions', provider='openai',
                  base_url='https://example.invalid', context_window=6000, max_tokens=512)
    call = AssistantMessage(content=[ToolCall(id='x', name='check', arguments={})],
        api=model.api, provider=model.provider, model=model.id, timestamp=0)
    result = ToolResultMessage(tool_call_id='x', tool_name='check',
        content=[TextContent(text='evidence 1234567 ' * 800)], timestamp=0)
    context = Context(messages=[UserMessage(content='Only analyze. ' * 450, timestamp=0), call, result])
    before = context.model_dump_json()
    import threading
    signal = threading.Event()
    compactor = RequestCompactor()
    calls = []
    async def summary(*_args):
        calls.append(True)
        if mode == 'raised':
            raise asyncio.CancelledError()
        if mode == 'signal':
            signal.set()
        return AssistantMessage(content=[TextContent(text='incomplete evidence')],
            api=model.api, provider=model.provider, model=model.id, timestamp=0,
            stop_reason='aborted' if mode == 'aborted' else 'stop')
    monkeypatch.setattr('openprogram.providers.complete_simple', summary)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(compactor.prepare(context, model, SimpleStreamOptions(max_tokens=512, signal=signal)))
    assert context.model_dump_json() == before
    assert not compactor._cache
    assert len(calls) == 1


@pytest.mark.parametrize('requested,expected', [(None, [3000, 250]), (100, [100, 100])])
def test_fallback_resolves_its_own_default_output_cap(monkeypatch, requested, expected):
    from types import SimpleNamespace
    import importlib
    primary = Model(id='primary', name='fixture', api='openai-completions', provider='openai',
        base_url='https://example.invalid', context_window=12000, max_tokens=8192)
    fallback = primary.model_copy(update={'id': 'fallback', 'context_window': 1000})
    dispatched = []
    async def provider_stream(provider, model, context, options, **kwargs):
        dispatched.append(options.max_tokens)
        if model.id == 'primary':
            raise ConnectionError('primary unavailable')
        yield EventDone(reason='stop', message=AssistantMessage(content=[TextContent(text='ok')],
            api=model.api, provider=model.provider, model=model.id, timestamp=0))
    monkeypatch.setattr('openprogram.providers.api_registry.resolve_api_provider_snapshot',
        lambda model: SimpleNamespace(provider=object(), supports_idempotency_key=False))
    monkeypatch.setattr('openprogram.providers.utils.failover.resolve_fallback_models', lambda model: [fallback])
    monkeypatch.setattr(importlib.import_module('openprogram.providers.stream'),
                        'stream_simple_with_provider', provider_stream)
    async def run():
        events = agent_loop([UserMessage(content='hello', timestamp=0)],
            AgentContext(tools=[], memory_prefetch=''),
            AgentLoopConfig(model=primary, convert_to_llm=lambda messages: messages, max_tokens=requested))
        await events.result()
    asyncio.run(run())
    assert dispatched == expected


def test_fallback_rechecks_actual_model_window(monkeypatch):
    from types import SimpleNamespace
    import pytest
    primary = Model(id='primary', name='fixture', api='openai-completions', provider='openai',
        base_url='https://example.invalid', context_window=12000, max_tokens=512)
    fallback = primary.model_copy(update={'id': 'fallback', 'context_window': 6000})
    dispatched = []
    async def provider_stream(provider, model, context, options, **kwargs):
        dispatched.append(model.id)
        if model.id == 'primary':
            raise ConnectionError('primary unavailable')
        yield EventDone(reason='stop', message=AssistantMessage(content=[TextContent(text='ok')],
            api=model.api, provider=model.provider, model=model.id, timestamp=0))
    monkeypatch.setattr('openprogram.providers.api_registry.resolve_api_provider_snapshot',
        lambda model: SimpleNamespace(provider=object(), supports_idempotency_key=False))
    monkeypatch.setattr('openprogram.providers.utils.failover.resolve_fallback_models', lambda model: [fallback])
    import importlib
    monkeypatch.setattr(importlib.import_module('openprogram.providers.stream'),
                        'stream_simple_with_provider', provider_stream)
    async def run():
        events = agent_loop([UserMessage(content=' '.join(f'evidence_{i}' for i in range(2000)), timestamp=0)],
            AgentContext(tools=[], memory_prefetch=''),
            AgentLoopConfig(model=primary, convert_to_llm=lambda messages: messages, max_tokens=512))
        with pytest.raises(ValueError, match='Protected request'):
            await events.result()
    asyncio.run(run())
    assert dispatched == ['primary']
