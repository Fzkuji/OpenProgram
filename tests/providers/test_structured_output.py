import copy
import threading

import pytest

from openprogram.providers import JsonSchemaOutput
from openprogram.providers import structured_output as structured_output_module
from openprogram.providers import api_registry
from openprogram.providers.api_registry import register_api_provider
from openprogram.providers.structured_output import (
    StructuredOutputSchemaError,
    StructuredOutputValidationError,
    StructuredOutputUnsupportedError,
    negotiate_structured_output,
    normalize_response_format,
    parse_and_validate_json,
)
from openprogram.providers.types import Model, Tool


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_json_schema_output_is_public_provider_api():
    assert JsonSchemaOutput(schema=SCHEMA).type == "json_schema"


def test_capability_types_and_lookup_are_public_provider_api():
    import openprogram.providers as providers

    assert providers.StructuredOutputCapabilities().native == "unknown"
    assert providers.get_structured_output_capabilities("missing-api").native == "unknown"


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
        id="third-party-test",
        name="Third-party test",
        api="openai-completions",
        provider="openrouter",
        base_url="https://example.invalid",
    )
    capabilities = api_registry.get_structured_output_capabilities(model.api)

    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        negotiate_structured_output(model, capabilities, normalize_response_format(SCHEMA), [])
    assert exc.value.code == "unsupported"

    prompt_output = normalize_response_format({
        "type": "json_schema",
        "schema": SCHEMA,
        "fallback": "prompt",
    })
    assert negotiate_structured_output(model, capabilities, prompt_output, []).mode == "prompt"


def _capabilities(**overrides):
    values = {
        "native": "unknown",
        "streaming": True,
        "with_tools": False,
        "strict_tool": False,
        "schema_profile": "none",
    }
    values.update(overrides)
    return structured_output_module.StructuredOutputCapabilities(**values)


def test_auto_unknown_is_unsupported_but_verified_strict_tool_uses_hidden_tool():
    model = Model(
        id="unknown-model",
        name="Unknown model",
        api="unknown-api",
        provider="unknown-provider",
        base_url="https://example.invalid",
    )
    output = normalize_response_format(SCHEMA)

    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(model, _capabilities(), output, [])

    plan = negotiate_structured_output(
        model,
        _capabilities(strict_tool=True),
        output,
        [],
    )
    assert plan.mode == "tool"
    assert plan.submit_tool_name == "__openprogram_submit_json"


@pytest.mark.parametrize(
    ("tool_choice", "parallel_tool_calls"),
    [("none", False), ("required", True)],
)
def test_hidden_tool_does_not_override_conflicting_caller_controls(
    tool_choice,
    parallel_tool_calls,
):
    model = Model(
        id="unknown-model",
        name="Unknown model",
        api="unknown-api",
        provider="unknown-provider",
        base_url="https://example.invalid",
    )
    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(
            model,
            _capabilities(strict_tool=True),
            normalize_response_format(SCHEMA),
            [],
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
        )


def test_hidden_tool_rejects_reserved_name_collision():
    model = Model(
        id="unknown-model",
        name="Unknown model",
        api="unknown-api",
        provider="unknown-provider",
        base_url="https://example.invalid",
    )
    collision = Tool(
        name="__openprogram_submit_json",
        description="Caller tool",
        parameters={"type": "object"},
    )

    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(
            model,
            _capabilities(strict_tool=True),
            normalize_response_format(SCHEMA),
            [collision],
        )


def test_strict_tool_kill_switch_disables_hidden_auto(monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_STRICT_TOOLS", "0")
    model = Model(
        id="callable",
        name="Codex",
        api="openai-codex",
        provider="openai-codex",
        base_url="https://example.invalid",
    )

    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(
            model,
            _capabilities(strict_tool=True, schema_profile="openai_strict"),
            normalize_response_format(SCHEMA),
            [],
        )


def test_negotiation_deep_copies_original_and_provider_schemas():
    output = normalize_response_format(SCHEMA)
    plan = negotiate_structured_output(
        Model(
            id="native-model",
            name="Native model",
            api="native-api",
            provider="native-provider",
            base_url="https://example.invalid",
            structured_output=True,
        ),
        _capabilities(native="supported"),
        output,
        [],
    )

    plan.provider_schema["properties"]["answer"]["minimum"] = 0
    plan.original_schema["properties"]["answer"]["maximum"] = 10

    assert "minimum" not in output.schema["properties"]["answer"]
    assert "maximum" not in output.schema["properties"]["answer"]


def test_lossy_native_projection_reports_first_bounded_schema_path():
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "integer", "minimum": 0}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    original = copy.deepcopy(schema)
    model = Model(
        id="gpt-test",
        name="GPT test",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        structured_output=True,
    )

    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        negotiate_structured_output(
            model,
            api_registry.get_structured_output_capabilities(model.api),
            normalize_response_format(schema),
            [],
        )

    assert exc.value.issues == [{
        "code": "unsupported_schema_constraint",
        "path": "/properties/answer/minimum",
        "schema_path": "/properties/answer/minimum",
        "message": "Provider schema profile would change this constraint",
    }]
    assert schema == original


def test_capability_registration_replaces_provider_and_capabilities_together(monkeypatch):
    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    first = object()
    second = object()
    first_capabilities = _capabilities(native="supported")
    second_capabilities = _capabilities(strict_tool=True)

    register_api_provider("test-api", first, first_capabilities)
    register_api_provider("test-api", second, second_capabilities)

    assert api_registry.get_api_provider("test-api") is second
    assert api_registry.get_structured_output_capabilities("test-api") == second_capabilities
    assert api_registry.get_structured_output_capabilities("missing-api") == _capabilities()


def test_provider_transform_preserves_replaced_capabilities(monkeypatch):
    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)

    class Wrapped:
        def __init__(self, provider):
            self.provider = provider

    first = object()
    first_capabilities = _capabilities(native="supported")
    register_api_provider("test-api", first, first_capabilities)
    api_registry.configure_provider_transform(lambda _api, provider: Wrapped(provider))

    assert api_registry.get_api_provider("test-api").provider is first
    assert api_registry.get_structured_output_capabilities("test-api") == first_capabilities

    second = object()
    second_capabilities = _capabilities(strict_tool=True)
    register_api_provider("test-api", second, second_capabilities)
    assert api_registry.get_api_provider("test-api").provider is second
    assert api_registry.get_structured_output_capabilities("test-api") == second_capabilities


def test_registry_entry_reads_remain_coherent_during_replacement(monkeypatch):
    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)
    pairs = [
        (object(), _capabilities(native="supported")),
        (object(), _capabilities(strict_tool=True)),
    ]
    allowed = {(id(provider), capabilities) for provider, capabilities in pairs}
    failures = []

    def writer():
        for index in range(500):
            register_api_provider("test-api", *pairs[index % 2])

    def reader():
        for _ in range(500):
            with api_registry._registry_lock:
                value = api_registry._registry.get("test-api")
                if value is None:
                    continue
                entry = api_registry._entry(value)
                if (id(entry.provider), entry.structured_output) not in allowed:
                    failures.append((entry.provider, entry.structured_output))

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []


def test_builtin_capabilities_are_explicit_and_unknown_adapters_fail_closed():
    openai = api_registry.get_structured_output_capabilities("openai-responses")
    codex = api_registry.get_structured_output_capabilities("openai-codex")
    gemini_subscription = api_registry.get_structured_output_capabilities(
        "gemini-subscription"
    )

    assert openai.native == "supported"
    assert openai.strict_tool is True
    assert openai.with_tools is True
    assert api_registry.get_structured_output_capabilities("anthropic-messages").native == "supported"
    assert api_registry.get_structured_output_capabilities("google-generative-ai").native == "supported"
    assert api_registry.get_structured_output_capabilities("bedrock-converse-stream").native == "supported"
    assert api_registry.get_structured_output_capabilities("azure-openai-responses").native == "supported"
    assert codex.native == "unknown"
    assert codex.strict_tool is True
    assert gemini_subscription.native == "unknown"
    assert gemini_subscription.strict_tool is False
