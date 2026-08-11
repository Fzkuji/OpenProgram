from openprogram.providers.google import google
from openprogram.providers.google.google import _build_config
import asyncio

import pytest

from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import AgentContext, AgentLoopConfig
from openprogram.providers.api_registry import get_structured_output_capabilities
from openprogram.providers.structured_output import (
    StructuredOutputUnsupportedError,
    negotiate_structured_output,
    normalize_response_format,
)
from openprogram.providers.types import Context, EventError, Model, SimpleStreamOptions, UserMessage


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_google_maps_schema_to_literal_generation_config_without_mutation():
    model = Model(
        id="gemini-test",
        name="Gemini test",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    output = normalize_response_format(SCHEMA)

    config = _build_config(
        model,
        Context(),
        SimpleStreamOptions(response_format=output),
    )
    wire = config.model_dump(exclude_none=True, by_alias=True)

    assert wire["responseMimeType"] == "application/json"
    assert wire["responseJsonSchema"] == SCHEMA
    assert output.schema == SCHEMA


def test_google_preserves_incomplete_and_refusal_finish_reasons():
    assert google._map_finish_reason("MAX_TOKENS") == "length"
    assert google._map_finish_reason("SAFETY") == "error"


def test_google_rejects_first_unsupported_sdk_schema_path_before_credentials():
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "integer", "not": {"const": 0}},
        },
        "required": ["answer"],
        "additionalProperties": False,
    }
    model = Model(
        id="gemini-test",
        name="Gemini test",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        structured_output=True,
    )
    output = normalize_response_format({
        "type": "json_schema",
        "schema": schema,
        "fallback": "none",
    })
    credentials = []
    provider_calls = []

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
                get_api_key=lambda provider: credentials.append(provider),
                convert_to_llm=lambda messages: messages,
            ),
            stream_fn=stream_fn,
        )
        return await stream.result()

    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        asyncio.run(run())
    assert exc.value.issues[0]["path"] == "/properties/answer/not"
    assert credentials == []
    assert provider_calls == []


def test_google_sdk_schema_validation_preserves_property_names_that_match_aliases():
    schema = {
        "type": "object",
        "properties": {"$ref": {"type": "string"}},
        "required": ["$ref"],
        "additionalProperties": False,
    }
    model = Model(
        id="gemini-test",
        name="Gemini test",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        structured_output=True,
    )
    output = normalize_response_format({
        "type": "json_schema",
        "schema": schema,
        "fallback": "none",
    })

    plan = negotiate_structured_output(
        model,
        get_structured_output_capabilities(model.api),
        output,
        [],
    )
    assert plan.mode == "native"
    assert plan.provider_schema == schema


def test_google_real_prompt_feedback_block_is_terminal_error(monkeypatch):
    from google.genai import types as gtypes
    from google.genai.types import BlockedReason

    blocked = gtypes.GenerateContentResponse(
        candidates=[],
        prompt_feedback=gtypes.GenerateContentResponsePromptFeedback(
            block_reason=BlockedReason.SAFETY,
        ),
    )

    class Models:
        async def generate_content_stream(self, **kwargs):
            async def chunks():
                yield blocked
            return chunks()

    class Client:
        def __init__(self, **kwargs):
            self.aio = type("Aio", (), {"models": Models()})()

    from google import genai
    monkeypatch.setattr(genai, "Client", Client)
    model = Model(
        id="gemini-test",
        name="Gemini test",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )

    async def consume():
        return [event async for event in google.stream_simple(
            model,
            Context(),
            SimpleStreamOptions(api_key="test"),
        )]

    events = asyncio.run(consume())
    assert isinstance(events[-1], EventError)
    assert events[-1].reason == "error"
