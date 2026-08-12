import asyncio
from types import SimpleNamespace

import pytest

from openprogram.providers.openai_completions import openai_completions
from openprogram.providers.anthropic import anthropic
from openprogram.providers.openai_responses.openai_responses import _build_params
from openprogram.providers.azure_openai_responses.azure_openai_responses import (
    _build_params as _build_azure_params,
)
from openprogram.providers.structured_output import (
    StructuredOutputUnsupportedError,
    negotiate_structured_output,
    normalize_response_format,
)
from openprogram.providers.api_registry import get_structured_output_capabilities
from openprogram.providers._shared.openai_responses import process_responses_stream
from openprogram.providers.amazon_bedrock.amazon_bedrock import _map_stop_reason_bedrock
from openprogram.providers.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    Tool,
    Usage,
    UserMessage,
)
from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import AgentContext, AgentLoopConfig


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _model(api):
    return Model(
        id="gpt-test",
        name="GPT test",
        api=api,
        provider="openai",
        base_url="https://api.openai.com/v1",
    )


def test_responses_builder_maps_normalized_schema_to_text_format():
    output = normalize_response_format({
        "type": "json_schema",
        "schema": SCHEMA,
        "name": "answer",
        "description": "An answer",
    })

    params = _build_params(_model("openai-responses"), Context(), {"response_format": output})

    assert params["text"]["format"] == {
        "type": "json_schema",
        "name": "answer",
        "description": "An answer",
        "strict": True,
        "schema": SCHEMA,
    }


def test_responses_builder_combines_literal_native_format_with_ordinary_tools():
    context = Context(tools=[Tool(
        name="lookup",
        description="Look up a value",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
    )])
    params = _build_params(
        _model("openai-responses"),
        context,
        {"response_format": normalize_response_format(SCHEMA)},
    )

    assert params["text"] == {"format": {
        "type": "json_schema",
        "name": "response",
        "strict": True,
        "schema": SCHEMA,
    }}
    assert params["tools"] == [{
        "type": "function",
        "name": "lookup",
        "description": "Look up a value",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
        "strict": True,
    }]


def test_azure_responses_builder_maps_literal_text_format():
    params = _build_azure_params(
        _model("azure-openai-responses").model_copy(update={
            "provider": "azure-openai-responses",
            "base_url": "https://unit.openai.azure.com/openai/v1",
        }),
        Context(),
        {"response_format": normalize_response_format(SCHEMA)},
        "deployment",
    )

    assert params["text"] == {"format": {
        "type": "json_schema",
        "name": "response",
        "strict": True,
        "schema": SCHEMA,
    }}


def test_azure_native_requires_explicit_deployment_support_metadata():
    capabilities = get_structured_output_capabilities("azure-openai-responses")
    model = _model("azure-openai-responses").model_copy(update={
        "provider": "azure-openai-responses",
        "base_url": "https://unit.openai.azure.com/openai/v1",
    })
    output = normalize_response_format({
        "type": "json_schema",
        "schema": SCHEMA,
        "fallback": "none",
    })

    assert capabilities.native == "unknown"
    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(model, capabilities, output, [])
    assert negotiate_structured_output(
        model.model_copy(update={"structured_output": True}),
        capabilities,
        output,
        [],
    ).mode == "native"


def test_community_openai_endpoint_fails_closed_before_payload_building():
    model = _model("openai-responses").model_copy(update={
        "base_url": "https://community.example/v1",
        "structured_output": True,
    })

    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(
            model,
            get_structured_output_capabilities(model.api),
            normalize_response_format(SCHEMA),
            [],
        )


def test_lossy_schema_rejection_precedes_credentials_and_provider_stream():
    credential_calls = []
    provider_calls = []
    model = _model("openai-responses").model_copy(update={"structured_output": True})
    output = normalize_response_format({
        "type": "object",
        "properties": {"answer": {"type": "integer", "minimum": 0}},
        "required": ["answer"],
        "additionalProperties": False,
    })

    async def stream_fn(*args):
        provider_calls.append(args)
        if False:
            yield None

    async def run():
        stream = agent_loop(
            [UserMessage(content="answer", timestamp=1)],
            AgentContext(tools=[]),
            AgentLoopConfig(
                model=model,
                response_format=output,
                get_api_key=lambda _provider: credential_calls.append(_provider),
                convert_to_llm=lambda messages: messages,
            ),
            stream_fn=stream_fn,
        )
        return await stream.result()

    with pytest.raises(StructuredOutputUnsupportedError):
        asyncio.run(run())
    assert credential_calls == []
    assert provider_calls == []


def test_native_provider_terminal_reasons_preserve_refusal_and_incomplete():
    class Collector:
        def push(self, event):
            pass

    async def events():
        yield {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message"},
        }
        yield {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "cannot comply"}],
            },
        }
        yield {
            "type": "response.completed",
            "response": {"status": "completed"},
        }

    model = _model("openai-responses")
    output = AssistantMessage(
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        timestamp=0,
    )
    asyncio.run(process_responses_stream(events(), output, Collector(), model))

    assert output.stop_reason == "error"
    assert anthropic._STOP_REASON_MAP["refusal"] == "error"
    assert anthropic._STOP_REASON_MAP["max_tokens"] == "length"
    assert _map_stop_reason_bedrock("content_filtered") == "error"
    assert _map_stop_reason_bedrock("max_tokens") == "length"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [("max_output_tokens", "length"), ("content_filter", "error")],
)
def test_real_openai_response_incomplete_event_preserves_reason(reason, expected):
    from openai.types.responses import Response, ResponseIncompleteEvent
    from openai.types.responses.response import IncompleteDetails

    response = Response.model_construct(
        id="resp_test",
        created_at=0.0,
        model="gpt-5",
        object="response",
        output=[],
        parallel_tool_calls=False,
        tool_choice="none",
        tools=[],
        status="incomplete",
        incomplete_details=IncompleteDetails(reason=reason),
    )
    event = ResponseIncompleteEvent(
        type="response.incomplete",
        sequence_number=1,
        response=response,
    )

    class Collector:
        def push(self, event):
            pass

    async def events():
        yield event

    model = _model("openai-responses")
    output = AssistantMessage(
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        timestamp=0,
    )
    asyncio.run(process_responses_stream(events(), output, Collector(), model))
    assert output.stop_reason == expected


def test_chat_completions_maps_normalized_schema_to_response_format(monkeypatch):
    captured = {}

    class EmptyStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def __aiter__(self):
            async def one_stop_chunk():
                yield SimpleNamespace(
                    usage=None,
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(
                            reasoning_content=None,
                            reasoning=None,
                            content=None,
                            tool_calls=None,
                        ),
                        finish_reason="stop",
                    )],
                )
            return one_stop_chunk()

    class Completions:
        async def create(self, **params):
            captured.update(params)
            return EmptyStream()

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(openai_completions._openai, "AsyncOpenAI", Client)
    options = SimpleStreamOptions(
        api_key="test-key",
        response_format=normalize_response_format(SCHEMA),
    )
    context = Context(tools=[Tool(
        name="lookup",
        description="Look up a value",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
    )])

    async def consume():
        return [event async for event in openai_completions.stream_simple(
            _model("openai-completions"), context, options
        )]

    asyncio.run(consume())

    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "response",
            "strict": True,
            "schema": SCHEMA,
        },
    }
    assert captured["tools"] == [{
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look up a value",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }]


def test_anthropic_merges_json_schema_with_existing_output_config(monkeypatch):
    captured = {}

    class Captured(Exception):
        pass

    def on_payload(params, model):
        captured.update(params)
        raise Captured

    monkeypatch.setattr(anthropic, "_build_client", lambda *args, **kwargs: (object(), False))
    model = _model("anthropic-messages").model_copy(update={
        "provider": "anthropic",
        "id": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "reasoning": True,
    })
    context = Context(tools=[Tool(
        name="lookup",
        description="Look up a value",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
    )])
    options = SimpleStreamOptions(
        api_key="test-key",
        reasoning="medium",
        on_payload=on_payload,
        response_format=normalize_response_format(SCHEMA),
    )

    async def consume():
        async for _ in anthropic.stream_simple(model, context, options):
            pass

    try:
        asyncio.run(consume())
    except Captured:
        pass

    assert captured["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert captured["tools"] == [{
        "name": "lookup",
        "description": "Look up a value",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
        "strict": True,
        "cache_control": {"type": "ephemeral"},
    }]
