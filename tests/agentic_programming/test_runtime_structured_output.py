import asyncio
import time

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.structured_output import (
    StructuredOutputSchemaError,
    StructuredOutputValidationError,
    StructuredOutputUnsupportedError,
)
from openprogram.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    Model,
    TextContent,
)


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_callable_runtime_returns_validated_python_value_and_forwards_schema():
    seen = []

    def call(content, model="test", response_format=None):
        seen.append(response_format)
        return '{"answer": 7}'

    result = Runtime(call=call, model="dummy").exec("question", response_format=SCHEMA)

    assert result == {"answer": 7}
    assert seen == [SCHEMA]


def test_invalid_schema_fails_before_callable_runs():
    calls = []
    runtime = Runtime(call=lambda *args, **kwargs: calls.append(1), model="dummy")

    with pytest.raises(StructuredOutputSchemaError):
        runtime.exec("question", response_format={"type": "not-a-type"})

    assert calls == []


def test_invalid_result_is_not_retried_as_transport_failure():
    calls = []

    def call(content, model="test", response_format=None):
        calls.append(1)
        return '{"answer": "wrong"}'

    runtime = Runtime(call=call, model="dummy", max_retries=3)
    response_format = {
        "type": "json_schema",
        "schema": SCHEMA,
        "max_validation_retries": 0,
    }
    with pytest.raises(StructuredOutputValidationError) as exc:
        runtime.exec("question", response_format=response_format)

    assert exc.value.code == "validation_failed"
    assert calls == [1]


def test_validation_failure_gets_one_bounded_semantic_repair():
    seen_content = []

    def call(content, model="test", response_format=None):
        seen_content.append(content)
        if len(seen_content) == 1:
            return '{"answer": "wrong"}'
        return '{"answer": 11}'

    result = Runtime(call=call, model="dummy", max_retries=3).exec(
        "question", response_format=SCHEMA
    )

    assert result == {"answer": 11}
    assert len(seen_content) == 2
    repair_text = seen_content[1][-1]["text"]
    assert "validation_failed" in repair_text
    assert len(repair_text) < 4000


def test_async_exec_returns_validated_python_value():
    async def call(content, model="test", response_format=None):
        return '{"answer": 9}'

    runtime = Runtime(call=call, model="dummy")
    assert asyncio.run(runtime.async_exec("question", response_format=SCHEMA)) == {"answer": 9}


def test_unknown_provider_is_rejected_before_stream_call():
    calls = []

    async def stream(model, context, options=None):
        calls.append(1)
        if False:
            yield None

    runtime = Runtime(call=lambda *args, **kwargs: "unused", model="dummy")
    runtime.api_model = Model(
        id="third-party-test",
        name="Third-party test",
        api="openai-completions",
        provider="openrouter",
        base_url="https://example.invalid",
    )

    with pytest.raises(StructuredOutputUnsupportedError):
        runtime.exec("question", response_format=SCHEMA, stream_fn=stream)
    assert calls == []


def test_explicit_prompt_fallback_adds_schema_instruction():
    seen = {}

    async def stream(model, context, options=None):
        seen["system"] = context.system_prompt
        message = AssistantMessage(
            content=[TextContent(text='{"answer": 5}')],
            api=model.api,
            provider=model.provider,
            model=model.id,
            timestamp=int(time.time() * 1000),
        )
        yield EventStart(partial=message)
        yield EventDone(reason="stop", message=message)

    runtime = Runtime(call=lambda *args, **kwargs: "unused", model="dummy")
    runtime.api_model = Model(
        id="third-party-test",
        name="Third-party test",
        api="openai-completions",
        provider="openrouter",
        base_url="https://example.invalid",
    )
    response_format = {
        "type": "json_schema",
        "schema": SCHEMA,
        "fallback": "prompt",
        "max_validation_retries": 0,
    }

    result = runtime.exec("question", response_format=response_format, stream_fn=stream)

    assert result == {"answer": 5}
    assert "Return only one complete JSON value" in seen["system"]
    assert '"additionalProperties":false' in seen["system"]
