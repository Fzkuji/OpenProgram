import pytest

from openprogram.providers import JsonSchemaOutput
from openprogram.providers.structured_output import (
    StructuredOutputSchemaError,
    StructuredOutputValidationError,
    StructuredOutputUnsupportedError,
    negotiate_structured_output,
    normalize_response_format,
    parse_and_validate_json,
)
from openprogram.providers.types import Model


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_json_schema_output_is_public_provider_api():
    assert JsonSchemaOutput(schema=SCHEMA).type == "json_schema"


def test_normalize_bare_schema_without_mutating_caller():
    original = {**SCHEMA, "properties": dict(SCHEMA["properties"])}

    output = normalize_response_format(original)
    output.schema["properties"]["answer"]["minimum"] = 0

    assert output.name == "response"
    assert output.fallback == "auto"
    assert "minimum" not in original["properties"]["answer"]


def test_normalize_envelope_and_reject_invalid_schema_before_request():
    output = normalize_response_format({
        "type": "json_schema",
        "schema": SCHEMA,
        "name": "answer_1",
        "fallback": "prompt",
        "max_validation_retries": 0,
    })
    assert output.name == "answer_1"
    assert output.max_validation_retries == 0

    with pytest.raises(StructuredOutputSchemaError) as exc:
        normalize_response_format({"type": "object", "properties": {"x": {"type": "not-a-type"}}})
    assert exc.value.code == "invalid_schema"


@pytest.mark.parametrize("field,value", [
    ("name", "bad name"),
    ("description", 3),
    ("strict", "yes"),
    ("fallback", "silent"),
    ("max_validation_retries", True),
    ("max_validation_retries", 2),
])
def test_normalize_rejects_invalid_control_fields(field, value):
    envelope = {"type": "json_schema", "schema": SCHEMA, field: value}
    with pytest.raises(StructuredOutputSchemaError):
        normalize_response_format(envelope)


@pytest.mark.parametrize("raw", [
    '```json\n{"answer": 1}\n```',
    '{"answer": 1} trailing',
    '{"answer": NaN}',
    '{"answer": Infinity}',
])
def test_parser_rejects_non_json_wrappers_and_non_finite_numbers(raw):
    output = normalize_response_format(SCHEMA)
    with pytest.raises(StructuredOutputValidationError) as exc:
        parse_and_validate_json(raw, output)
    assert exc.value.code == "invalid_json"


def test_validation_returns_typed_value_and_bounded_deterministic_issues():
    output = normalize_response_format(SCHEMA)
    assert parse_and_validate_json('{"answer": 3}', output) == {"answer": 3}

    with pytest.raises(StructuredOutputValidationError) as exc:
        parse_and_validate_json('{"answer": "no", "extra": true}', output)

    assert exc.value.code == "validation_failed"
    assert [issue["path"] for issue in exc.value.issues] == ["", "/answer"]
    assert all(len(issue["message"]) <= 500 for issue in exc.value.issues)


def test_negotiation_is_unknown_safe_and_prompt_fallback_is_explicit():
    model = Model(
        id="claude-test",
        name="Claude test",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://example.invalid",
    )

    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        negotiate_structured_output(model, normalize_response_format(SCHEMA))
    assert exc.value.code == "unsupported"

    prompt_output = normalize_response_format({
        "type": "json_schema",
        "schema": SCHEMA,
        "fallback": "prompt",
    })
    assert negotiate_structured_output(model, prompt_output) == "prompt"
