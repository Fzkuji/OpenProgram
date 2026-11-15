from __future__ import annotations

import asyncio
import time

import pytest

from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
from openprogram.models.providers.structured_output import (
    StructuredOutputGenerationError,
    StructuredOutputValidationError,
    normalize_response_format,
)
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventError,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    UserMessage,
)
from openprogram.models.providers.utils.errors import ExecInterrupt


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _message(text: str, *, stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="completion",
        provider="fake",
        model="fake",
        stop_reason=stop_reason,
        timestamp=int(time.time() * 1000),
    )


def _stream(replies: list[AssistantMessage], seen_contexts: list) -> callable:
    call = 0

    def stream_fn(model, context, options):
        nonlocal call
        seen_contexts.append(context)
        if call >= len(replies):
            raise AssertionError("unexpected provider repair call")
        message = replies[call]
        call += 1

        async def events():
            empty = message.model_copy(update={"content": [TextContent(text="")]})
            yield EventStart(partial=empty)
            yield EventTextStart(content_index=0, partial=empty)
            text = message.content[0].text
            yield EventTextDelta(content_index=0, delta=text, partial=message)
            yield EventTextEnd(content_index=0, content=text, partial=message)
            if message.stop_reason in ("error", "aborted"):
                yield EventError(reason=message.stop_reason, error=message)
            else:
                yield EventDone(reason=message.stop_reason, message=message)

        return events()

    return stream_fn


def _config(*, retries: int = 1) -> AgentLoopConfig:
    return AgentLoopConfig(
        model=Model(
            id="fake",
            name="Fake",
            api="completion",
            provider="fake",
            base_url="https://example.invalid",
        ),
        response_format=normalize_response_format(
            {
                "type": "json_schema",
                "schema": SCHEMA,
                "fallback": "prompt",
                "max_validation_retries": retries,
            }
        ),
        convert_to_llm=lambda messages: messages,
    )


async def _run(replies: list[AssistantMessage], *, retries: int = 1):
    seen_contexts = []
    stream = agent_loop(
        [UserMessage(content="answer", timestamp=1)],
        AgentContext(tools=[]),
        _config(retries=retries),
        stream_fn=_stream(replies, seen_contexts),
    )
    events = []
    async for event in stream:
        events.append(event)
    return await stream.result(), events, seen_contexts


def _assistant_events(events):
    return [
        event.assistant_message_event
        for event in events
        if event.type == "message_update"
    ]


def test_invalid_candidate_emits_retry_then_one_valid_terminal_result():
    messages, events, contexts = asyncio.run(
        _run([_message('{"answer":"bad"}'), _message(' { "answer" : 7 } ')])
    )

    inner = _assistant_events(events)
    structured = [event for event in inner if event.type.startswith("structured_")]
    assert [event.type for event in structured] == [
        "structured_output_retry",
        "structured_output_end",
    ]
    assert structured[0].attempt == 1
    assert structured[0].next_attempt == 2
    assert structured[0].issues[0]["code"] == "schema_violation"
    assert structured[1].attempt == 2
    assert structured[1].mode == "prompt"
    assert structured[1].value == {"answer": 7}

    text_events = [event for event in inner if event.type.startswith("text_")]
    assert [event.output_attempt for event in text_events] == [1, 1, 1, 2, 2, 2]
    assert [event.type for event in inner][-2:] == ["structured_output_end", "done"]

    assistants = [message for message in messages if message.role == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].structured_output == {"answer": 7}
    assert assistants[0].structured_output_mode == "prompt"
    assert assistants[0].structured_output_attempt == 2
    assert assistants[0].content == [TextContent(text='{"answer":7}')]
    assert len(contexts) == 2
    assert "validation_failed" in contexts[1].messages[-1].content
    assert '{"answer":"bad"}' not in contexts[1].messages[-1].content


def test_second_invalid_candidate_has_no_done_and_persists_no_assistant():
    seen_contexts = []
    events = []

    async def collect():
        stream = agent_loop(
            [UserMessage(content="answer", timestamp=1)],
            AgentContext(tools=[]),
            _config(retries=1),
            stream_fn=_stream(
                [_message('{"answer":"bad"}'), _message('{"answer":false}')],
                seen_contexts,
            ),
        )
        with pytest.raises(StructuredOutputValidationError) as exc:
            async for event in stream:
                events.append(event)
        assert exc.value.code == "validation_failed"

    asyncio.run(collect())
    inner = _assistant_events(events)
    assert [event.type for event in inner if event.type.startswith("structured_")] == [
        "structured_output_retry"
    ]
    assert not any(event.type == "done" for event in inner)
    assert not any(
        event.type == "message_end" and event.message.role == "assistant"
        for event in events
    )
    assert len(seen_contexts) == 2


def test_retry_disabled_raises_after_one_provider_call():
    with pytest.raises(StructuredOutputValidationError) as exc:
        asyncio.run(_run([_message('{"answer":"bad"}')], retries=0))

    assert exc.value.code == "validation_failed"


def test_provider_eof_without_raw_terminal_does_not_synthesize_success():
    message = _message('{"answer":7}')

    def eof_stream(model, context, options):
        async def events():
            empty = message.model_copy(update={"content": [TextContent(text="")]})
            yield EventStart(partial=empty)
            yield EventTextStart(content_index=0, partial=empty)
            yield EventTextDelta(
                content_index=0,
                delta='{"answer":7}',
                partial=message,
            )
            yield EventTextEnd(
                content_index=0,
                content='{"answer":7}',
                partial=message,
            )

        return events()

    async def collect():
        stream = agent_loop(
            [UserMessage(content="answer", timestamp=1)],
            AgentContext(tools=[]),
            _config(),
            stream_fn=eof_stream,
        )
        seen = []
        with pytest.raises(StructuredOutputGenerationError) as exc:
            async for event in stream:
                seen.append(event)
        assert exc.value.code == "incomplete"
        return seen

    events = asyncio.run(collect())
    assert not any(
        getattr(getattr(event, "assistant_message_event", None), "type", None)
        in {"structured_output_end", "done"}
        for event in events
    )


def test_provider_empty_eof_is_typed_incomplete():
    def eof_stream(model, context, options):
        async def events():
            if False:
                yield

        return events()

    async def collect():
        stream = agent_loop(
            [UserMessage(content="answer", timestamp=1)],
            AgentContext(tools=[]),
            _config(),
            stream_fn=eof_stream,
        )
        with pytest.raises(StructuredOutputGenerationError) as exc:
            async for _ in stream:
                pass
        assert exc.value.code == "incomplete"

    asyncio.run(collect())


def test_retry_event_and_prompt_do_not_copy_invalid_candidate_value():
    secret = "candidate-secret-value"
    _, events, contexts = asyncio.run(
        _run([
            _message('{"answer":"' + secret + '"}'),
            _message('{"answer":3}'),
        ])
    )

    retry = next(
        event for event in _assistant_events(events)
        if event.type == "structured_output_retry"
    )
    assert secret not in str(retry.issues)
    assert secret not in contexts[1].messages[-1].content
    assert len(retry.issues) <= 20
    assert all(len(issue["message"]) <= 500 for issue in retry.issues)


@pytest.mark.parametrize(
    ("stop_reason", "error_type", "code"),
    [
        ("error", StructuredOutputGenerationError, "refusal"),
        ("aborted", ExecInterrupt, None),
    ],
)
def test_non_validation_terminal_never_starts_repair(stop_reason, error_type, code):
    seen_contexts = []

    async def collect():
        stream = agent_loop(
            [UserMessage(content="answer", timestamp=1)],
            AgentContext(tools=[]),
            _config(retries=1),
            stream_fn=_stream([_message("", stop_reason=stop_reason)], seen_contexts),
        )
        with pytest.raises(error_type) as exc:
            async for _ in stream:
                pass
        if code is not None:
            assert exc.value.code == code

    asyncio.run(collect())
    assert len(seen_contexts) == 1

@pytest.mark.parametrize("ending", ["transport", "cancel", "eof", "done"])
def test_each_provider_request_has_one_identified_terminal_event(monkeypatch, ending):
    import importlib
    loop = importlib.import_module("openprogram.runtime.agent.loop")
    seen = []
    monkeypatch.setattr(loop.streaming, "emit_safe", lambda kind, origin, payload=None: seen.append((kind, payload)))

    def provider(model, context, options):
        async def events():
            yield EventStart(partial=_message("partial"))
            if ending == "transport":
                raise RuntimeError("transport disconnected")
            if ending == "cancel":
                raise ExecInterrupt("cancelled")
            if ending == "done":
                yield EventDone(reason="stop", message=_message('{"answer":7}'))
        return events()

    async def run():
        stream = agent_loop([UserMessage(content="answer", timestamp=1)], AgentContext(tools=[]),
                            _config(retries=0), stream_fn=provider)
        async for _ in stream:
            pass
        await stream.result()

    if ending == "transport":
        with pytest.raises(RuntimeError, match="transport disconnected"):
            asyncio.run(run())
    elif ending == "cancel":
        with pytest.raises(ExecInterrupt):
            asyncio.run(run())
    elif ending == "eof":
        with pytest.raises(StructuredOutputGenerationError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert [kind for kind, _ in seen] == ["model.response_started", "model.response_completed"]
    assert seen[0][1]["request_id"] == seen[1][1]["request_id"]
    assert seen[0][1]["request_id"]
    expected = {"transport":"failed", "cancel":"cancelled", "eof":"incomplete", "done":"completed"}[ending]
    assert seen[1][1]["status"] == expected
    assert seen[1][1]["is_error"] is (ending != "done")

def test_structured_repair_requests_have_distinct_balanced_identities(monkeypatch):
    import importlib
    loop = importlib.import_module("openprogram.runtime.agent.loop")
    seen = []
    monkeypatch.setattr(loop.streaming, "emit_safe", lambda kind, origin, payload=None: seen.append((kind, payload)))
    asyncio.run(_run([_message('{"answer":"bad"}'), _message('{"answer":7}')]))
    assert [kind for kind, _ in seen] == ["model.response_started", "model.response_completed"] * 2
    assert seen[0][1]["request_id"] == seen[1][1]["request_id"]
    assert seen[2][1]["request_id"] == seen[3][1]["request_id"]
    assert seen[0][1]["request_id"] != seen[2][1]["request_id"]


def test_truncated_value_is_regenerated_without_partial_candidate():
    messages, events, contexts = asyncio.run(_run([
        _message('{"answer":', stop_reason='length'), _message('{"answer":7}')
    ]))
    assert messages[-1].structured_output == {'answer': 7}
    retry = next(e for e in _assistant_events(events) if e.type == 'structured_output_retry')
    assert retry.issues[0]['code'] == 'incomplete'
    assert len(contexts) == 2
    assert not any(m.role == 'assistant' and m.content == [TextContent(text='{"answer":')]
                   for m in contexts[1].messages)


def test_truncation_and_validation_share_recovery_allowance():
    with pytest.raises(StructuredOutputValidationError):
        asyncio.run(_run([_message('{"answer":', stop_reason='length'),
                          _message('{"answer":"bad"}')], retries=1))


@pytest.mark.parametrize('partial', [False, True])
def test_eof_recovers_to_complete_value(partial):
    calls = []
    async def provider(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            if partial:
                yield EventStart(partial=_message('{"answer":'))
            return
        message = _message('{"answer":7}')
        yield EventDone(reason='stop', message=message)
    async def run():
        stream = agent_loop([UserMessage(content='answer', timestamp=1)],
                            AgentContext(tools=[], memory_prefetch=''), _config(retries=2),
                            stream_fn=provider)
        async for _ in stream:
            pass
        return await stream.result()
    result = asyncio.run(run())
    assert result[-1].structured_output == {'answer': 7}
    assert len(calls) == 2


@pytest.mark.parametrize('ending', ['length', 'eof', 'invalid'])
def test_exhausted_recovery_is_paused(ending):
    from openprogram.models.providers.utils.recovery import RecoveryState, current_recovery
    state = RecoveryState(limit=2)
    calls = []
    async def provider(model, context, options):
        calls.append(1)
        if ending == 'eof':
            return
        message = _message('{' if ending == 'length' else '{"answer":"bad"}',
                           stop_reason='length' if ending == 'length' else 'stop')
        yield EventDone(reason=message.stop_reason, message=message)
    async def run():
        token = current_recovery.set(state)
        try:
            stream = agent_loop([UserMessage(content='answer', timestamp=1)],
                                AgentContext(tools=[], memory_prefetch=''), _config(retries=2),
                                stream_fn=provider)
            with pytest.raises((StructuredOutputGenerationError, StructuredOutputValidationError)):
                async for _ in stream:
                    pass
        finally:
            current_recovery.reset(token)
    asyncio.run(run())
    assert len(calls) == 3
    assert state.used == 2
    assert state.phase == 'paused'


def test_eof_preserves_completed_tool_and_never_executes_partial_tool():
    from openprogram.models.providers.types import AgentTool, AgentToolResult
    from openprogram.models.providers.types import ToolCall
    calls, executed = [], []
    async def execute(call_id, arguments, *_args):
        executed.append(call_id)
        return AgentToolResult(content=[TextContent(text='saved evidence')])
    async def provider(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            message = _message('').model_copy(update={'content': [ToolCall(id='complete', name='read', arguments={})], 'stop_reason': 'toolUse'})
            yield EventDone(reason='toolUse', message=message)
        elif len(calls) == 2:
            yield EventStart(partial=_message('').model_copy(update={'content': [ToolCall(id='partial', name='read', arguments={})]}))
        else:
            assert any(m.role == 'toolResult' and m.tool_call_id == 'complete' for m in context.messages)
            yield EventDone(reason='stop', message=_message('{"answer":7}'))
    async def run():
        tool = AgentTool(name='read', label='read', description='read', parameters={'type': 'object'}, execute=execute)
        stream = agent_loop([UserMessage(content='answer', timestamp=1)],
                            AgentContext(tools=[tool], memory_prefetch=''), _config(retries=2), stream_fn=provider)
        async for _ in stream:
            pass
        return await stream.result()
    result = asyncio.run(run())
    assert result[-1].structured_output == {'answer': 7}
    assert executed == ['complete']
    assert len(calls) == 3


def test_cancelled_eof_does_not_retry_and_marks_cancelled():
    from openprogram.models.providers.utils.recovery import RecoveryState, current_recovery
    state = RecoveryState(limit=2)
    calls = []
    async def run():
        cancel = asyncio.Event()
        async def provider(model, context, options):
            calls.append(1)
            cancel.set()
            if False:
                yield
        token = current_recovery.set(state)
        try:
            stream = agent_loop([UserMessage(content='answer', timestamp=1)],
                                AgentContext(tools=[], memory_prefetch=''), _config(retries=2),
                                cancel_event=cancel, stream_fn=provider)
            async for _ in stream:
                pass
            await stream.result()
        finally:
            current_recovery.reset(token)
    asyncio.run(run())
    assert calls == [1]
    assert state.phase == 'cancelled'


def test_returned_provider_error_is_not_marked_completed():
    from openprogram.models.providers.utils.recovery import RecoveryState, current_recovery
    state = RecoveryState(limit=2)
    message = _message('', stop_reason='error').model_copy(update={'error_message': 'network failed'})
    async def run():
        token = current_recovery.set(state)
        try:
            stream = agent_loop([UserMessage(content='answer', timestamp=1)],
                                AgentContext(tools=[], memory_prefetch=''), _config(retries=2),
                                stream_fn=_stream([message], []))
            async for _ in stream:
                pass
            result = await stream.result()
            assert result[-1].stop_reason == 'error'
        finally:
            current_recovery.reset(token)
    asyncio.run(run())
    assert state.phase != 'completed'
