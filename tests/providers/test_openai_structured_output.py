import asyncio
from types import SimpleNamespace

from openprogram.providers.openai_completions import openai_completions
from openprogram.providers.anthropic import anthropic
from openprogram.providers.openai_responses.openai_responses import _build_params
from openprogram.providers.structured_output import normalize_response_format
from openprogram.providers.types import Context, Model, SimpleStreamOptions


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

    async def consume():
        return [event async for event in openai_completions.stream_simple(
            _model("openai-completions"), Context(), options
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
        "reasoning": True,
    })
    options = SimpleStreamOptions(
        api_key="test-key",
        reasoning="medium",
        on_payload=on_payload,
        response_format=normalize_response_format(SCHEMA),
    )

    async def consume():
        async for _ in anthropic.stream_simple(model, Context(), options):
            pass

    try:
        asyncio.run(consume())
    except Captured:
        pass

    assert captured["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
