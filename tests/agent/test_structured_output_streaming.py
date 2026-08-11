from __future__ import annotations

import asyncio
import time

import pytest

from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import AgentContext, AgentLoopConfig
from openprogram.providers.structured_output import (
    StructuredOutputGenerationError,
    StructuredOutputValidationError,
    normalize_response_format,
)
from openprogram.providers.types import (
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
        ("length", StructuredOutputGenerationError, "incomplete"),
        ("error", StructuredOutputGenerationError, "refusal"),
        ("aborted", RuntimeError, None),
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
