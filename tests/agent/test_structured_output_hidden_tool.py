from __future__ import annotations

import asyncio
import importlib
import time

import pytest

from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
)
from openprogram.providers.structured_output import (
    HIDDEN_SUBMIT_TOOL_NAME,
    StructuredOutputValidationError,
    normalize_response_format,
)
from openprogram.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _assistant(content) -> AssistantMessage:
    has_calls = any(isinstance(block, ToolCall) for block in content)
    return AssistantMessage(
        content=content,
        api="openai-codex",
        provider="openai-codex",
        model="fake",
        stop_reason="toolUse" if has_calls else "stop",
        timestamp=int(time.time() * 1000),
    )


def _submit(answer=4) -> ToolCall:
    return ToolCall(
        id="submit",
        name=HIDDEN_SUBMIT_TOOL_NAME,
        arguments={"answer": answer},
    )


def _make_stream_fn(
    replies: list[AssistantMessage], seen_contexts: list, seen_options: list
):
    call = 0

    def stream_fn(model, context, options):
        nonlocal call
        seen_contexts.append(context)
        seen_options.append(options)
        if call >= len(replies):
            raise AssertionError("agent loop requested an unexpected provider round")
        message = replies[call]
        call += 1

        async def events():
            yield EventStart(partial=message)
            yield EventDone(reason=message.stop_reason, message=message)

        return events()

    return stream_fn


def _config(**overrides) -> AgentLoopConfig:
    values = {
        "model": Model(
            id="fake",
            name="Fake",
            api="openai-codex",
            provider="openai-codex",
            base_url="https://example.invalid",
        ),
        "response_format": normalize_response_format(SCHEMA),
        "convert_to_llm": lambda messages: messages,
    }
    values.update(overrides)
    return AgentLoopConfig(
        **values,
    )


async def _run_loop(replies, tools, **config_overrides):
    seen_contexts = []
    seen_options = []
    stream = agent_loop(
        [UserMessage(content="answer", timestamp=1)],
        AgentContext(tools=tools),
        _config(**config_overrides),
        stream_fn=_make_stream_fn(replies, seen_contexts, seen_options),
    )
    return await stream.result(), seen_contexts, seen_options


def test_hidden_submit_tool_is_terminal_and_is_not_executed(monkeypatch):
    executed = []
    approval_checks = []
    from openprogram.functions._runtime import _registry
    loop_module = importlib.import_module("openprogram.agent.agent_loop")

    monkeypatch.setattr(
        loop_module,
        "decide_tool_gate",
        lambda event: approval_checks.append(event),
    )
    assert HIDDEN_SUBMIT_TOOL_NAME not in _registry

    async def execute(call_id, arguments, cancel_event, on_update):
        executed.append(arguments)
        return AgentToolResult(content=[TextContent(text="executed")])

    ordinary = AgentTool(
        name="ordinary",
        description="Ordinary tool",
        parameters={"type": "object", "properties": {}},
        label="ordinary",
        execute=execute,
    )
    messages, seen_contexts, seen_options = asyncio.run(
        _run_loop([_assistant([_submit()])], [ordinary])
    )
    final = next(
        message for message in reversed(messages) if message.role == "assistant"
    )

    assert final.structured_output == {"answer": 4}
    assert final.structured_output_mode == "tool"
    assert executed == []
    assert approval_checks == []
    assert HIDDEN_SUBMIT_TOOL_NAME not in _registry
    assert not any(isinstance(message, ToolResultMessage) for message in messages)
    assert [tool.name for tool in seen_contexts[0].tools] == [
        "ordinary",
        HIDDEN_SUBMIT_TOOL_NAME,
    ]
    assert [tool.name for tool in [ordinary]] == ["ordinary"]
    assert seen_options[0].parallel_tool_calls is False
    assert seen_options[0].response_format is None


def test_submit_must_be_the_only_tool_call_in_its_message():
    executed = []

    async def execute(call_id, arguments, cancel_event, on_update):
        executed.append(arguments)
        return AgentToolResult(content=[TextContent(text="executed")])

    ordinary = AgentTool(
        name="ordinary",
        description="Ordinary tool",
        parameters={"type": "object", "properties": {}},
        label="ordinary",
        execute=execute,
    )
    reply = _assistant(
        [
            ToolCall(id="ordinary", name="ordinary", arguments={}),
            _submit(),
        ]
    )

    with pytest.raises(StructuredOutputValidationError) as exc:
        asyncio.run(_run_loop([reply], [ordinary]))

    assert exc.value.code == "mixed_submission"
    assert executed == []


def test_ordinary_tool_executes_before_a_later_hidden_submission():
    executed = []

    async def execute(call_id, arguments, cancel_event, on_update):
        executed.append(call_id)
        return AgentToolResult(content=[TextContent(text="executed")])

    ordinary = AgentTool(
        name="ordinary",
        description="Ordinary tool",
        parameters={"type": "object", "properties": {}},
        label="ordinary",
        execute=execute,
    )
    messages, _, _ = asyncio.run(
        _run_loop(
            [
                _assistant([ToolCall(id="ordinary", name="ordinary", arguments={})]),
                _assistant([_submit()]),
            ],
            [ordinary],
        )
    )
    final = next(
        message for message in reversed(messages) if message.role == "assistant"
    )

    assert executed == ["ordinary"]
    assert final.structured_output == {"answer": 4}
    assert final.structured_output_mode == "tool"


def test_failed_ordinary_tool_can_be_followed_by_hidden_submission():
    async def execute(call_id, arguments, cancel_event, on_update):
        raise ValueError("expected tool failure")

    ordinary = AgentTool(
        name="ordinary",
        description="Ordinary tool",
        parameters={"type": "object", "properties": {}},
        label="ordinary",
        execute=execute,
    )
    messages, _, _ = asyncio.run(
        _run_loop(
            [
                _assistant([ToolCall(id="ordinary", name="ordinary", arguments={})]),
                _assistant([_submit()]),
            ],
            [ordinary],
        )
    )

    failed_result = next(
        message for message in messages if isinstance(message, ToolResultMessage)
    )
    final = next(
        message for message in reversed(messages) if message.role == "assistant"
    )
    assert failed_result.is_error is True
    assert final.structured_output == {"answer": 4}


def test_iteration_cap_without_hidden_submission_is_missing_submission():
    ordinary = AgentTool(
        name="ordinary",
        description="Ordinary tool",
        parameters={"type": "object", "properties": {}},
        label="ordinary",
        execute=lambda *args: asyncio.sleep(
            0, result=AgentToolResult(content=[TextContent(text="executed")])
        ),
    )

    with pytest.raises(StructuredOutputValidationError) as exc:
        asyncio.run(
            _run_loop(
                [_assistant([ToolCall(id="ordinary", name="ordinary", arguments={})])],
                [ordinary],
                max_iterations=1,
            )
        )

    assert exc.value.code == "missing_submission"


def test_cross_provider_unknown_fallback_never_receives_native_options(monkeypatch):
    import openprogram.providers.utils.failover as failover_module

    loop_module = importlib.import_module("openprogram.agent.agent_loop")

    primary = Model(
        id="primary",
        name="Primary",
        api="openai-completions",
        provider="openai",
        base_url="https://api.openai.com/v1",
        structured_output=True,
    )
    unknown = Model(
        id="fallback",
        name="Fallback",
        api="openai-completions",
        provider="openrouter",
        base_url="https://example.invalid",
        structured_output=True,
    )
    calls = []

    def base_stream(model, context, options):
        calls.append((model.provider, options.response_format))

        async def events():
            if model.provider == "openai":
                raise ConnectionError("primary unavailable")
            message = AssistantMessage(
                content=[TextContent(text='{"answer": 4}')],
                api=model.api,
                provider=model.provider,
                model=model.id,
                timestamp=1,
            )
            yield EventStart(partial=message)
            yield EventDone(reason="stop", message=message)

        return events()

    monkeypatch.setattr(loop_module, "_default_stream_simple", base_stream)
    monkeypatch.setattr(
        failover_module,
        "resolve_fallback_models",
        lambda _model: [unknown],
    )

    async def run():
        stream = agent_loop(
            [UserMessage(content="answer", timestamp=1)],
            AgentContext(tools=[]),
            _config(model=primary),
        )
        return await stream.result()

    with pytest.raises(ConnectionError):
        asyncio.run(run())

    assert [provider for provider, _options in calls] == ["openai"]


def test_hidden_submission_is_validated_against_the_original_schema():
    with pytest.raises(StructuredOutputValidationError) as exc:
        asyncio.run(_run_loop([_assistant([_submit("not-an-integer")])], []))

    assert exc.value.code == "validation_failed"


def test_hidden_tool_mode_rejects_text_without_submission():
    with pytest.raises(StructuredOutputValidationError) as exc:
        asyncio.run(_run_loop([_assistant([TextContent(text='{"answer": 4}')])], []))

    assert exc.value.code == "missing_submission"
