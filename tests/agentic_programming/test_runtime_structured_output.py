import asyncio

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.structured_output import (
    StructuredOutputSchemaError,
    StructuredOutputValidationError,
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
    with pytest.raises(StructuredOutputValidationError) as exc:
        runtime.exec("question", response_format=SCHEMA)

    assert exc.value.code == "validation_failed"
    assert calls == [1]


def test_async_exec_returns_validated_python_value():
    async def call(content, model="test", response_format=None):
        return '{"answer": 9}'

    runtime = Runtime(call=call, model="dummy")
    assert asyncio.run(runtime.async_exec("question", response_format=SCHEMA)) == {"answer": 9}
