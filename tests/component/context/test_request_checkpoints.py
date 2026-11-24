"""Request checkpoints preserve instructions while allowing long work to continue."""
import asyncio

import pytest

from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
from openprogram.runtime.context.compaction.request import RequestCompactor
from openprogram.runtime.context.compaction.summarize import Summarizer
from openprogram.runtime.context.token_accounting.tokens import _text_tokens, estimate_history_tokens
from openprogram.models.providers.types import (
    AssistantMessage, Context, EventDone, Model, SimpleStreamOptions,
    TextContent, ToolCall, ToolResultMessage, UserMessage,
)
from tests.support.summary_stream import install_summary_reply, sectioned


def model():
    return Model(id='checkpoint', name='fixture', api='openai-completions',
                 provider='openai', base_url='https://example.invalid',
                 context_window=6000, max_tokens=512)


def answer(text):
    m = model()
    return AssistantMessage(content=[TextContent(text=text)], api=m.api,
                            provider=m.provider, model=m.id, timestamp=0)


def _reply(prompt, *, server_tool=False):
    """A finished reply whose usage reports a prompt of ``prompt`` tokens."""
    from openprogram.models.providers.types import ServerToolUse, Usage
    reply = answer('done')
    if server_tool:
        reply.content.insert(0, ServerToolUse(id='ws-1', name='web_search', input={}))
    reply.usage = Usage(input=prompt, output=10, tokens_reported=True)
    return reply


def test_request_size_is_the_last_count_plus_the_change():
    """A matching count moves the size by what the estimate missed, added once."""
    request = Context(messages=[UserMessage(content='review ' * 2600, timestamp=0)])
    estimate = _text_tokens('review ' * 2600) + 4
    compactor = RequestCompactor()
    roomy = model().model_copy(update={'max_tokens': 8192})
    async def run():
        first = SimpleStreamOptions()
        await compactor.prepare(request, roomy, first)
        compactor.note_reply(roomy, _reply(estimate + 300), estimate)
        second = SimpleStreamOptions()
        await compactor.prepare(request, roomy, second)
        assert second.max_tokens == first.max_tokens - 300
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['server_tool', 'over_window', 'error'])
def test_a_count_of_several_passes_is_not_the_request_size(kind):
    """A reply that ran provider-side tools reports its passes summed (xAI's
    web search: three passes, three times the prompt); a count over the
    window or from a failed reply measured no request. None of them changes
    the size, and the request that fits is sent as it is."""
    request = Context(messages=[UserMessage(content='review ' * 2600, timestamp=0)])
    estimate = _text_tokens('review ' * 2600) + 4
    compactor = RequestCompactor()
    reply = _reply(3 * estimate if kind != 'over_window' else 7000,
                   server_tool=kind == 'server_tool')
    if kind == 'error':
        reply.stop_reason = 'error'
    roomy = model().model_copy(update={'max_tokens': 8192})
    async def run():
        first = SimpleStreamOptions()
        await compactor.prepare(request, roomy, first)
        compactor.note_reply(roomy, reply, estimate)
        second = SimpleStreamOptions()
        prepared = await compactor.prepare(request, roomy, second)
        assert second.max_tokens == first.max_tokens
        assert prepared.messages == request.messages
    asyncio.run(run())


def install_summary(monkeypatch):
    calls = []
    async def summary(m, context, options):
        assert (_text_tokens(context.system_prompt or '')
                + estimate_history_tokens(context.messages) + options.max_tokens < m.context_window)
        calls.append(context)
        return answer(sectioned('Goal: inspect only. Pending: verify the failing check. Evidence remains unverified.'))
    install_summary_reply(monkeypatch, summary)
    return calls


def test_agent_loop_compacts_old_assistant_text_and_keeps_raw_history(monkeypatch):
    calls = install_summary(monkeypatch)
    old = answer('Investigation detail 123456789 ' * 2500)
    latest = answer('Current finding: check failed; no edits performed.')
    user = UserMessage(content='Only analyze; ask before editing.', timestamp=0)
    incoming = UserMessage(content='Continue the inspection.', timestamp=0)
    seen = []
    async def stream(m, context, options):
        seen.append(context)
        yield EventDone(reason='stop', message=answer('Inspection complete.'))
    async def run():
        context = AgentContext(messages=[user, old, latest], tools=[], memory_prefetch='')
        events = agent_loop([incoming], context,
            AgentLoopConfig(model=model(), max_tokens=512, convert_to_llm=lambda messages: messages),
            stream_fn=stream)
        await events.result()
        assert old in context.messages
    asyncio.run(run())
    assert calls and len(seen) == 1
    assert user in seen[0].messages and incoming in seen[0].messages
    assert latest in seen[0].messages and old not in seen[0].messages
    assert estimate_history_tokens(seen[0].messages) < 5188


def test_completed_argument_history_checkpoint_is_cached_and_invalidated(monkeypatch):
    calls = install_summary(monkeypatch)
    owner = answer('')
    owner.content = [ToolCall(id='old-call', name='inspect', arguments={'query': 'long query ' * 5000})]
    result = ToolResultMessage(tool_call_id='old-call', tool_name='inspect',
                               content=[TextContent(text='failed')], is_error=True, timestamp=0)
    user = UserMessage(content='Do not modify files.', timestamp=0)
    latest = answer('Need another check.')
    raw = Context(messages=[user, owner, result, latest])
    before = raw.model_dump_json()
    compactor = RequestCompactor()
    async def run():
        first = await compactor.prepare(raw, model(), SimpleStreamOptions(max_tokens=512))
        count = len(calls)
        second = await compactor.prepare(raw, model(), SimpleStreamOptions(max_tokens=512))
        assert len(calls) == count and second.messages == first.messages
        assert user in first.messages and latest in first.messages
        assert owner not in first.messages and result not in first.messages
        changed = raw.model_copy(deep=True)
        changed.messages[0].content = 'Only read files; do not execute commands.'
        third = await compactor.prepare(changed, model(), SimpleStreamOptions(max_tokens=512))
        assert len(calls) > count and changed.messages[0] in third.messages
    asyncio.run(run())
    assert raw.model_dump_json() == before


def test_persistent_summary_covers_oversized_source_in_bounded_requests(monkeypatch):
    calls = install_summary(monkeypatch)
    messages = []
    for i in range(6):
        messages += [{'role': 'user', 'content': f'requirement {i}'},
                     {'role': 'assistant', 'content': f'BEGIN_{i} ' + 'details 123456789 ' * 1500 + f' END_{i}'}]
    summarizer = Summarizer(max_summary_tokens=512)
    result = asyncio.run(summarizer.summarise(messages=messages, model=model(), keep_recent_tokens=100))
    assert result.summary_text and result.error is None
    text = '\n'.join(b.text for c in calls for m in c.messages for b in m.content)
    for i in range(result.cut_idx // 2):
        assert f'BEGIN_{i}' in text and f'END_{i}' in text
    assert len(calls) > 1


def test_summary_overflow_retries_smaller_without_omitting_source(monkeypatch):
    seen = []
    async def summary(m, context, options):
        text = context.messages[0].content[0].text
        seen.append(text)
        if len(seen) == 1:
            return answer('').model_copy(update={'stop_reason': 'error', 'error_message': 'context_length_exceeded'})
        return answer(sectioned('Completed inspection; still need verification.'))
    install_summary_reply(monkeypatch, summary)
    messages = [{'role': role, 'content': f'{i} ' + ('detail ' * 300 if role == 'assistant' else 'inspect only')}
                for i in range(6) for role in ('user', 'assistant')]
    result = asyncio.run(Summarizer(max_summary_tokens=512).summarise(
        messages=messages, model=model(), keep_recent_tokens=10))
    assert result.summary_text and result.error is None
    assert len(seen) >= 3
    assert len(seen[1]) < len(seen[0])


def _fits(context, m, options):
    return (_text_tokens(context.system_prompt or '') + estimate_history_tokens(context.messages)
            <= m.context_window - options.max_tokens - max(256, m.context_window // 20))


@pytest.mark.parametrize('kind', ['missing_result', 'image', 'signed_thinking'])
def test_a_prefix_no_summary_may_cover_still_fits(monkeypatch, kind):
    """What a checkpoint may not summarise (an unanswered call, the turn's own
    signed reasoning) is fitted by the later steps instead; an earlier image
    becomes a note. The request is sent, history is untouched."""
    from openprogram.models.providers.types import ImageContent, ThinkingContent
    install_summary(monkeypatch)
    old = answer('large text ' * 6000)
    if kind == 'missing_result':
        old.content.append(ToolCall(id='unresolved', name='check', arguments={}))
    elif kind == 'image':
        user_content = [ImageContent(data='fixture', mime_type='image/png')]
    else:
        old.content.append(ThinkingContent(thinking='private', thinking_signature='signed'))
    first = UserMessage(content=user_content if kind == 'image' else 'Read only.', timestamp=0)
    messages = [first, old, answer('Current step.')]
    if kind == 'image':
        messages.append(UserMessage(content='Continue.', timestamp=0))
    raw = Context(messages=messages)
    before = raw.model_dump_json()
    options = SimpleStreamOptions(max_tokens=512)
    prepared = asyncio.run(RequestCompactor().prepare(raw, model(), options))
    assert _fits(prepared, model(), options)
    assert raw.model_dump_json() == before
    assert prepared.messages[-1] == raw.messages[-1]
    if kind == 'image':
        assert not any(getattr(b, 'type', None) == 'image'
                       for m in prepared.messages if not isinstance(m.content, str) for b in m.content)


def test_only_a_latest_message_over_the_window_fails():
    big = UserMessage(content='evidence ' * 9000, timestamp=0)
    with pytest.raises(ValueError, match='does not fit'):
        asyncio.run(RequestCompactor().prepare(Context(messages=[big]), model(),
                                               SimpleStreamOptions(max_tokens=512)))


@pytest.mark.parametrize('mode', ['cancelled', 'auth', 'overflow_forever'])
def test_failed_checkpoint_preserves_input_and_does_not_cache(monkeypatch, mode):
    """A summary that fails is skipped: the request is fitted without it and
    nothing is cached. A stop still stops."""
    calls = []
    async def summary(*args):
        calls.append(True)
        if mode == 'cancelled':
            raise asyncio.CancelledError()
        if mode == 'auth':
            raise PermissionError('authentication denied')
        raise ValueError('context_length_exceeded')
    install_summary_reply(monkeypatch, summary)
    raw = Context(messages=[UserMessage(content='Read only.', timestamp=0),
                            answer('large text ' * 6000), answer('Current step.')])
    before = raw.model_dump_json()
    compactor = RequestCompactor()
    options = SimpleStreamOptions(max_tokens=512)
    if mode == 'cancelled':
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(compactor.prepare(raw, model(), options))
    else:
        prepared = asyncio.run(compactor.prepare(raw, model(), options))
        assert _fits(prepared, model(), options)
        assert prepared.messages[-1] == raw.messages[-1]
    assert raw.model_dump_json() == before and compactor._checkpoint is None
    assert 0 < len(calls) <= 32
    if mode != 'overflow_forever':
        assert len(calls) == 1


def test_checkpoint_reuses_append_only_prefix_and_rechecks_model(monkeypatch):
    calls = install_summary(monkeypatch)
    user = UserMessage(content='Read only.', timestamp=0)
    raw = Context(messages=[user, answer('large text ' * 6000), answer('Current step.')])
    compactor = RequestCompactor()
    async def run():
        await compactor.prepare(raw, model(), SimpleStreamOptions(max_tokens=512))
        count = len(calls)
        extended = raw.model_copy(update={'messages': [*raw.messages, UserMessage(content='Continue.', timestamp=0)]})
        out = await compactor.prepare(extended, model(), SimpleStreamOptions(max_tokens=512))
        assert len(calls) == count and out.messages[-1] == extended.messages[-1]
        switched = model().model_copy(update={'id': 'other'})
        await compactor.prepare(extended, switched, SimpleStreamOptions(max_tokens=512))
        assert len(calls) > count
    asyncio.run(run())


def test_summary_keeps_call_credentials_but_not_main_request_identity(monkeypatch):
    from openprogram.runtime.context.compaction.bounded_summary import summarize_text
    async def summary(m, context, options):
        assert options.api_key == 'fixture'
        assert options.idempotency_key is None and options.session_id is None
        assert options.tool_choice is None and options.on_payload is None
        return answer('summary')
    install_summary_reply(monkeypatch, summary)
    opts = SimpleStreamOptions(api_key='fixture', idempotency_key='main-call',
                               session_id='main-session', tool_choice='required',
                               on_payload=lambda *_: pytest.fail('main request callback'))
    assert asyncio.run(summarize_text('source', model(), opts, instruction='summarize', max_tokens=100)) == 'summary'


def test_evidence_then_checkpoint_cache_tracks_original_prefix(monkeypatch):
    calls = install_summary(monkeypatch)
    owner = answer('old analysis ' * 4500)
    owner.content.append(ToolCall(id='evidence', name='inspect', arguments={}))
    result = ToolResultMessage(tool_call_id='evidence', tool_name='inspect',
                               content=[TextContent(text='long evidence ' * 4500)], timestamp=0)
    raw = Context(messages=[UserMessage(content='Read only.', timestamp=0), owner, result, answer('Current step.')])
    compactor = RequestCompactor()
    async def run():
        first = await compactor.prepare(raw, model(), SimpleStreamOptions(max_tokens=512))
        count = len(calls)
        second = await compactor.prepare(raw, model(), SimpleStreamOptions(max_tokens=512))
        assert len(calls) == count
        assert first.messages == second.messages
        appended = raw.model_copy(update={'messages': [*raw.messages, UserMessage(content='Continue.', timestamp=0)]})
        await compactor.prepare(appended, model(), SimpleStreamOptions(max_tokens=512))
        assert len(calls) == count
    asyncio.run(run())


def test_overflow_chunk_retry_covers_each_source_character(monkeypatch):
    from openprogram.runtime.context.compaction.bounded_summary import summarize_text
    original = ''.join(f'record-{i:05d};' for i in range(1000))
    pieces, options_seen = [], []
    attempts = 0
    async def summary(m, context, options):
        nonlocal attempts
        attempts += 1
        prompt = context.messages[0].content[0].text
        if attempts == 1:
            raise ValueError('context_length_exceeded')
        pieces.append(prompt.split('<source>\n', 1)[1].removesuffix('\n</source>'))
        options_seen.append(options.max_tokens)
        return answer('Prior evidence retained; further verification needed.')
    install_summary_reply(monkeypatch, summary)
    asyncio.run(summarize_text(original, model(), SimpleStreamOptions(),
                              instruction='Preserve findings.', max_tokens=512))
    assert ''.join(pieces) == original
    assert attempts > 2


def test_default_provider_path_keeps_checkpoint_across_tool_continuation(monkeypatch):
    from types import SimpleNamespace
    import importlib
    from openprogram.models.providers.types import AgentTool, AgentToolResult
    calls = install_summary(monkeypatch)
    counts = []
    requests = []
    async def stream(provider, m, context, options, **kwargs):
        counts.append(len(calls))
        requests.append(context)
        reply = answer('done')
        if len(requests) == 1:
            reply.content = [ToolCall(id='next', name='check', arguments={})]
            reply.stop_reason = 'toolUse'
        yield EventDone(reason=reply.stop_reason, message=reply)
    async def execute(*_args):
        return AgentToolResult(content=[TextContent(text='checked')])
    monkeypatch.setattr('openprogram.models.providers.catalog.api_registry.resolve_api_provider_snapshot',
                        lambda m: SimpleNamespace(provider=object(), supports_idempotency_key=False))
    monkeypatch.setattr('openprogram.models.providers.utils.failures.failover.resolve_fallback_models', lambda m: [])
    monkeypatch.setattr(importlib.import_module('openprogram.models.providers.streaming.stream'), 'stream_simple_with_provider', stream)
    async def run():
        context = AgentContext(messages=[UserMessage(content='Read only.', timestamp=0),
            answer('large text ' * 6000), answer('Current step.')], memory_prefetch='',
            tools=[AgentTool(name='check', label='check', description='check', parameters={'type':'object'}, execute=execute)])
        events = agent_loop([UserMessage(content='Continue.', timestamp=0)], context,
            AgentLoopConfig(model=model(), max_tokens=512, convert_to_llm=lambda messages: messages))
        await events.result()
    asyncio.run(run())
    assert len(requests) == 2
    assert counts[0] > 0 and counts[1] == counts[0]


def test_a_turn_is_measured_by_the_request_it_sends(monkeypatch):
    """The stored row of a reply holds its text; the request carries its
    tool calls and results too, and that is what decides compaction."""
    from openprogram.runtime.context.engine import DefaultContextEngine

    engine = DefaultContextEngine(auto_compact_pct=0.8)
    rows = [{'id': 'u1', 'role': 'user', 'content': 'check the sheet'},
            {'id': 'a1', 'role': 'assistant', 'content': 'Done.'}]
    rendered = [UserMessage(content='check the sheet', timestamp=0),
                answer('[tool call] read the sheet\n[result]\n' + 'row ' * 6000)]
    monkeypatch.setattr(engine, '_build_messages_from_dag', lambda **_kw: rendered)
    prep = engine.prepare(agent={}, session={}, history=rows, model=model(), tools=[], system_prompt='x')
    assert prep.budget.history == estimate_history_tokens(rendered)
    assert engine.should_auto_compact(prep)

