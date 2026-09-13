"""Contract tests for the reusable scripted test provider.

The helper lives in ``tests.component.providers.scripted_provider`` so tests can drive
the normal API-provider registry and agent loop without a network provider or
a ``stream_fn`` injection.
"""
from __future__ import annotations

import asyncio
import importlib
from collections.abc import Iterator

import pytest

from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import AgentContext, AgentLoopConfig, AgentTool, AgentToolResult
from openprogram.providers.api_registry import get_api_provider, register_api_provider
from openprogram.providers.types import (
    Context,
    EventError,
    Model,
    SimpleStreamOptions,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


_API = "scripted-test-api"


def _model() -> Model:
    return Model(
        id="scripted-test-model",
        name="Scripted test model",
        api=_API,
        provider="scripted-test-provider",
        base_url="http://scripted.invalid",
    )


@pytest.fixture
def registry_state() -> Iterator[object | None]:
    """Assert after dependent fixtures have restored the API registry."""
    previous = get_api_provider(_API)
    yield previous
    assert get_api_provider(_API) is previous


@pytest.fixture
def registered_provider(registry_state) -> Iterator:
    from tests.component.providers.scripted_provider import ScriptedProvider

    previous = registry_state
    provider = ScriptedProvider()
    register_api_provider(_API, provider)
    try:
        yield provider
    finally:
        if previous is None:
            from openprogram.providers import api_registry
            api_registry._registry.pop(_API, None)
        else:
            register_api_provider(_API, previous)


def test_registered_provider_streams_text_and_thinking_via_public_facade(
    registered_provider,
) -> None:
    """Removing facade dispatch or thinking event construction breaks this."""
    from tests.component.providers.scripted_provider import ScriptedText, ScriptedThinking
    from openprogram.providers import stream_simple

    registered_provider.add_response(
        ScriptedThinking("inspect inputs"), ScriptedText("answer")
    )

    async def drain():
        return [
            event async for event in stream_simple(
                _model(), Context(messages=[]), SimpleStreamOptions(api_key="test-key")
            )
        ]

    events = asyncio.run(drain())

    assert get_api_provider(_API) is registered_provider
    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    assert [
        (block.type, getattr(block, "thinking", getattr(block, "text", None)))
        for block in events[-1].message.content
    ] == [("thinking", "inspect inputs"), ("text", "answer")]


def test_registered_provider_streams_via_public_stream(registered_provider) -> None:
    """The non-simple public stream must use the same registry provider."""
    from tests.component.providers.scripted_provider import ScriptedText
    from openprogram.providers import stream

    registered_provider.add_response(ScriptedText("ordinary stream"))

    async def drain():
        return [
            event async for event in stream(
                _model(), Context(messages=[]), StreamOptions(api_key="test-key")
            )
        ]

    events = asyncio.run(drain())

    assert events[-1].message.content == [TextContent(text="ordinary stream")]
    assert registered_provider.calls[0].options == StreamOptions(api_key="test-key")


def test_registered_provider_uses_ordered_responses_and_surfaces_terminal_error(
    registered_provider,
) -> None:
    """Ignoring call order or terminal errors breaks the scripted sequence."""
    from tests.component.providers.scripted_provider import (
        ScriptedError,
        ScriptedText,
        ScriptedToolCall,
    )
    from openprogram.providers import stream_simple

    registered_provider.add_response(
        ScriptedText("checking"),
        ScriptedToolCall("lookup", {"id": "7"}, "call-7"),
    )
    registered_provider.add_response(
        ScriptedError(
            "quota exhausted",
            event_reason="error",
            error_reason="rate_limit",
            retryable=True,
            retry_after_s=7.5,
        )
    )

    async def drain():
        return [
            event async for event in stream_simple(
                _model(), Context(messages=[]), SimpleStreamOptions(api_key="test-key")
            )
        ]

    first = asyncio.run(drain())
    second = asyncio.run(drain())

    assert [event.type for event in first] == [
        "start", "text_start", "text_delta", "text_end",
        "toolcall_start", "toolcall_delta", "toolcall_end", "done",
    ]
    tool_events = [event for event in first if event.type.startswith("toolcall_")]
    assert [event.content_index for event in tool_events] == [1, 1, 1]
    assert tool_events[1].delta == '{"id": "7"}'
    assert first[-1].reason == "toolUse"
    assert isinstance(first[-1].message.content[1], ToolCall)
    assert first[-1].message.content[1].id == "call-7"
    assert isinstance(second[-1], EventError)
    assert second[-1].reason == "error"
    assert second[-1].error.error_message == "quota exhausted"
    assert second[-1].error.error_reason == "rate_limit"
    assert second[-1].error.error_retryable is True
    assert second[-1].error.error_retry_after_s == 7.5
    assert registered_provider.call_count == 2


def test_registered_provider_preserves_aborted_stop_reason(registered_provider) -> None:
    """Hard-coding an error stop reason loses a terminal abort's meaning."""
    from tests.component.providers.scripted_provider import ScriptedError
    from openprogram.providers import stream_simple

    registered_provider.add_response(ScriptedError("cancelled", event_reason="aborted"))

    async def drain():
        return [
            event async for event in stream_simple(
                _model(), Context(messages=[]), SimpleStreamOptions(api_key="test-key")
            )
        ]

    events = asyncio.run(drain())

    assert isinstance(events[-1], EventError)
    assert events[-1].reason == "aborted"
    assert events[-1].error.stop_reason == "aborted"


def test_agent_loop_executes_tool_through_registered_scripted_provider(
    registered_provider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing a stream_fn directly would not satisfy this registry-path test."""
    from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall

    monkeypatch.delenv("OPENPROGRAM_FALLBACK_MODELS", raising=False)
    stream_module = importlib.import_module("openprogram.providers.stream")
    monkeypatch.setattr(stream_module, "resolve_provider_key", lambda provider: None)
    class EmptyMemory:
        def search(self, query: str) -> str:
            return ""

    monkeypatch.setattr("openprogram.memory.get_backend", EmptyMemory)
    registered_provider.add_response(ScriptedToolCall("echo", {"value": "hi"}, "call-1"))
    registered_provider.add_response(ScriptedText("tool completed"))
    executed: list[tuple[str, dict]] = []

    async def echo(call_id, args, cancel_event, on_update) -> AgentToolResult:
        executed.append((call_id, args))
        return AgentToolResult(content=[TextContent(text=f"echo:{args['value']}")])

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Returns the supplied value.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        execute=echo,
    )
    config = AgentLoopConfig(
        model=_model(),
        convert_to_llm=lambda messages: [
            message for message in messages if getattr(message, "role", None) in {
                "user", "assistant", "toolResult"
            }
        ],
    )

    async def drain():
        events = []
        stream = agent_loop(
            [UserMessage(content="use echo", timestamp=0)],
            AgentContext(tools=[tool], memory_prefetch=""),
            config,
        )
        async for event in stream:
            events.append(event)
        return events

    events = asyncio.run(drain())

    assert executed == [("call-1", {"value": "hi"})]
    assert registered_provider.call_count == 2
    assert registered_provider.calls[0].options.api_key is None
    second_context = registered_provider.calls[1].context
    assert isinstance(second_context.messages[-1], ToolResultMessage)
    assert second_context.messages[-1].tool_call_id == "call-1"
    assert second_context.messages[-1].content == [TextContent(text="echo:hi")]
    assert [event.type for event in events if event.type.startswith("tool_execution")] == [
        "tool_execution_start",
        "tool_execution_end",
    ]
    assert events[-1].type == "agent_end"
    assert events[-1].messages[-1].content == [TextContent(text="tool completed")]


def test_registered_provider_restores_registry_after_fixture_teardown(
    registered_provider,
) -> None:
    """registry_state's final assertion runs after registered_provider's teardown."""
    assert get_api_provider(_API) is registered_provider
