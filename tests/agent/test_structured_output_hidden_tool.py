from __future__ import annotations

import asyncio
import importlib
import time

import pytest

from openprogram.agent.agent import Agent, AgentOptions
from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
)
from openprogram.providers import register_api_provider
from openprogram.providers import api_registry
from openprogram.providers.structured_output import (
    HIDDEN_SUBMIT_TOOL_NAME,
    StructuredOutputCapabilities,
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
    from openprogram.programs._runtime import _registry
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
        asyncio.run(_run_loop(
            [reply],
            [ordinary],
            response_format=normalize_response_format({
                "type": "json_schema",
                "schema": SCHEMA,
                "max_validation_retries": 0,
            }),
        ))

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

    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)

    primary_api = "cross-provider-primary-api"
    fallback_api = "cross-provider-unknown-api"

    primary = Model(
        id="primary",
        name="Primary",
        api=primary_api,
        provider="primary-provider",
        base_url="https://example.invalid",
        structured_output=True,
    )
    unknown = Model(
        id="fallback",
        name="Fallback",
        api=fallback_api,
        provider="unknown-provider",
        base_url="https://example.invalid",
        structured_output=True,
    )
    calls = []

    class Provider:
        requires_credentials = False

        def __init__(self, name):
            self.name = name

        def stream_simple(self, model, context, options):
            calls.append((self.name, options.response_format))

            async def events():
                raise ConnectionError("primary unavailable")
                yield

            return events()

    register_api_provider(
        primary_api,
        Provider("primary-native"),
        StructuredOutputCapabilities(native="supported", schema_profile="none"),
    )
    register_api_provider(fallback_api, Provider("fallback-unknown"))
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

    assert [provider for provider, _options in calls] == ["primary-native"]


def test_registry_replacement_cannot_split_negotiation_from_dispatch(monkeypatch):
    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", "off")
    calls = []

    class Provider:
        requires_credentials = False

        def __init__(self, name):
            self.name = name

        def stream_simple(self, model, context, options):
            calls.append((self.name, options.response_format))
            message = AssistantMessage(
                content=[TextContent(text='{"answer": 4}')],
                api=model.api,
                provider=model.provider,
                model=model.id,
                timestamp=1,
            )

            async def events():
                yield EventStart(partial=message)
                yield EventDone(reason="stop", message=message)

            return events()

    api = "replacement-race-api"
    original = Provider("original-native")
    replacement = Provider("replacement-unknown")
    register_api_provider(
        api,
        original,
        StructuredOutputCapabilities(native="supported", schema_profile="none"),
    )

    async def replace_after_negotiation(messages, _cancel_event):
        register_api_provider(api, replacement)
        return messages

    model = Model(
        id="race-model",
        name="Race model",
        api=api,
        provider="race-provider",
        base_url="https://example.invalid",
        structured_output=True,
    )

    async def run():
        stream = agent_loop(
            [UserMessage(content="answer", timestamp=1)],
            AgentContext(tools=[]),
            _config(model=model, transform_context=replace_after_negotiation),
        )
        return await stream.result()

    asyncio.run(run())

    assert calls == [("original-native", _config().response_format)]


def test_default_agent_dispatch_uses_the_negotiated_registry_snapshot(monkeypatch):
    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", "off")
    calls = []

    class Provider:
        requires_credentials = False

        def __init__(self, name):
            self.name = name

        def stream_simple(self, model, context, options):
            calls.append((self.name, options.response_format))
            message = AssistantMessage(
                content=[TextContent(text='{"answer": 4}')],
                api=model.api,
                provider=model.provider,
                model=model.id,
                timestamp=1,
            )

            async def events():
                yield EventStart(partial=message)
                yield EventDone(reason="stop", message=message)

            return events()

    api = "default-agent-snapshot-api"
    original = Provider("original-native")
    replacement = Provider("replacement-unknown")
    capabilities = StructuredOutputCapabilities(
        native="supported", schema_profile="none"
    )
    register_api_provider(api, original, capabilities)

    async def replace_after_negotiation(messages, _cancel_event):
        register_api_provider(api, replacement)
        return messages

    model = Model(
        id="agent-race-model",
        name="Agent race model",
        api=api,
        provider="race-provider",
        base_url="https://example.invalid",
        structured_output=True,
    )
    agent = Agent(
        AgentOptions(
            initial_state={"model": model},
            transform_context=replace_after_negotiation,
            response_format=normalize_response_format(SCHEMA),
        )
    )

    asyncio.run(agent.prompt("answer"))

    assert calls == [("original-native", normalize_response_format(SCHEMA))]


def test_failover_candidate_dispatch_uses_its_negotiated_registry_snapshot(monkeypatch):
    import openprogram.providers.utils.failover as failover_module

    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)
    calls = []

    primary_api = "snapshot-primary-api"
    fallback_api = "snapshot-fallback-api"

    class Provider:
        requires_credentials = False

        def __init__(self, name, on_stream=None):
            self.name = name
            self.on_stream = on_stream

        def stream_simple(self, model, context, options):
            calls.append((self.name, options.response_format))
            if self.on_stream is not None:
                self.on_stream()

            async def events():
                if self.name == "primary-native":
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

    fallback_original = Provider("fallback-native")
    fallback_replacement = Provider("fallback-unknown")

    def replace_fallback():
        register_api_provider(fallback_api, fallback_replacement)

    capabilities = StructuredOutputCapabilities(
        native="supported",
        schema_profile="none",
    )
    register_api_provider(
        primary_api,
        Provider("primary-native", replace_fallback),
        capabilities,
    )
    register_api_provider(fallback_api, fallback_original, capabilities)

    primary = Model(
        id="primary",
        name="Primary",
        api=primary_api,
        provider="primary-provider",
        base_url="https://example.invalid",
        structured_output=True,
    )
    fallback = Model(
        id="fallback",
        name="Fallback",
        api=fallback_api,
        provider="fallback-provider",
        base_url="https://example.invalid",
        structured_output=True,
    )
    monkeypatch.setattr(
        failover_module,
        "resolve_fallback_models",
        lambda _model: [fallback],
    )

    async def run():
        stream = agent_loop(
            [UserMessage(content="answer", timestamp=1)],
            AgentContext(tools=[]),
            _config(model=primary),
        )
        return await stream.result()

    asyncio.run(run())

    assert [name for name, _options in calls] == [
        "primary-native",
        "fallback-native",
    ]
    assert all(options is not None for _name, options in calls)


def test_hidden_submission_is_validated_against_the_original_schema():
    with pytest.raises(StructuredOutputValidationError) as exc:
        asyncio.run(_run_loop(
            [_assistant([_submit("not-an-integer")])],
            [],
            response_format=normalize_response_format({
                "type": "json_schema",
                "schema": SCHEMA,
                "max_validation_retries": 0,
            }),
        ))

    assert exc.value.code == "validation_failed"


def test_hidden_tool_mode_rejects_text_without_submission():
    with pytest.raises(StructuredOutputValidationError) as exc:
        asyncio.run(_run_loop(
            [_assistant([TextContent(text='{"answer": 4}')])],
            [],
            response_format=normalize_response_format({
                "type": "json_schema",
                "schema": SCHEMA,
                "max_validation_retries": 0,
            }),
        ))

    assert exc.value.code == "missing_submission"


def test_hidden_validation_failure_repairs_inside_one_agent_loop():
    messages, seen_contexts, _ = asyncio.run(
        _run_loop(
            [
                _assistant([_submit("not-an-integer")]),
                _assistant([_submit(8)]),
            ],
            [],
        )
    )

    assistants = [message for message in messages if message.role == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].structured_output == {"answer": 8}
    assert assistants[0].structured_output_attempt == 2
    assert len(seen_contexts) == 2


def test_mixed_submission_repairs_without_executing_ordinary_tool():
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
                _assistant([
                    ToolCall(id="ordinary", name="ordinary", arguments={}),
                    _submit(8),
                ]),
                _assistant([_submit(9)]),
            ],
            [ordinary],
        )
    )

    assert executed == []
    final = next(message for message in reversed(messages) if message.role == "assistant")
    assert final.structured_output == {"answer": 9}
    assert final.structured_output_attempt == 2
