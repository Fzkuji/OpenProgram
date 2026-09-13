from openprogram.providers.google import google
from openprogram.providers.google.google import _build_config
import asyncio

import pytest

from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import AgentContext, AgentLoopConfig
from openprogram.providers.api_registry import get_structured_output_capabilities
from openprogram.providers.structured_output import (
    StructuredOutputSchemaError,
    StructuredOutputUnsupportedError,
    negotiate_structured_output,
    normalize_response_format,
)
from openprogram.providers.types import Context, EventError, Model, SimpleStreamOptions, UserMessage
from openprogram.providers.types import EventDone

import json
import time


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _google_model():
    return Model(
        id="gemini-test",
        name="Gemini test",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        structured_output=True,
    )


def _negotiate_google(schema):
    return negotiate_structured_output(
        _google_model(),
        get_structured_output_capabilities("google-generative-ai"),
        normalize_response_format({
            "type": "json_schema",
            "schema": schema,
            "fallback": "none",
        }),
        [],
    )


def _nested_array_schema(depth):
    schema = {"type": "string"}
    for _ in range(depth):
        schema = {"type": "array", "items": schema}
    return schema


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


def test_google_schema_preflight_preserves_property_names_that_match_keywords():
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


@pytest.mark.parametrize(
    ("keyword", "schema"),
    [
        ("$id", {"$id": "urn:response", "type": "string"}),
        ("$defs", {"$defs": {"value": {"type": "string"}}, "type": "object"}),
        ("$ref", {
            "$defs": {"value": {"type": "string"}},
            "type": "object",
            "properties": {"value": {"$ref": "#/$defs/value"}},
        }),
        ("$anchor", {"$anchor": "response", "type": "string"}),
        ("type", {"type": "string"}),
        ("format", {"type": "string", "format": "date-time"}),
        ("title", {"type": "string", "title": "Response"}),
        ("description", {"type": "string", "description": "Response"}),
        ("enum", {"enum": ["yes", "no"]}),
        ("items", {"type": "array", "items": {"type": "string"}}),
        ("prefixItems", {"type": "array", "prefixItems": [{"type": "string"}]}),
        ("minItems", {"type": "array", "minItems": 1}),
        ("maxItems", {"type": "array", "maxItems": 2}),
        ("minimum", {"type": "number", "minimum": 0}),
        ("maximum", {"type": "number", "maximum": 1}),
        ("anyOf", {"anyOf": [{"type": "string"}, {"type": "number"}]}),
        ("oneOf", {"oneOf": [{"type": "string"}, {"type": "number"}]}),
        ("properties", {"type": "object", "properties": {"value": {"type": "string"}}}),
        ("additionalProperties", {"type": "object", "additionalProperties": False}),
        ("required", {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }),
        ("propertyOrdering", {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "propertyOrdering": ["value"],
        }),
    ],
)
def test_google_response_json_schema_accepts_documented_keyword(keyword, schema):
    assert keyword in str(schema)
    plan = _negotiate_google(schema)
    assert plan.mode == "native"
    assert plan.provider_schema == schema


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("default", "value"),
        ("example", "value"),
        ("pattern", "^value$"),
        ("nullable", True),
        ("minLength", 1),
        ("maxLength", 5),
        ("minProperties", 1),
        ("maxProperties", 2),
    ],
)
def test_google_response_json_schema_rejects_undocumented_keyword(keyword, value):
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string", keyword: value}},
    }
    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        _negotiate_google(schema)
    assert exc.value.issues[0]["path"] == f"/properties/answer/{keyword}"


@pytest.mark.parametrize(
    ("schema", "path"),
    [
        ({"type": "array", "prefixItems": [{"type": "string", "default": "x"}]},
         "/prefixItems/0/default"),
        ({"oneOf": [{"type": "string"}, {"type": "number", "default": 1}]},
         "/oneOf/1/default"),
        ({"$defs": {"value": {"type": "string", "default": "x"}}},
         "/$defs/value/default"),
        ({"type": "object", "additionalProperties": {"type": "string", "default": "x"}},
         "/additionalProperties/default"),
    ],
)
def test_google_response_json_schema_reports_nested_first_path(schema, path):
    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        _negotiate_google(schema)
    assert exc.value.issues[0]["path"] == path


def test_google_response_json_schema_rejects_ref_siblings():
    schema = {
        "$defs": {"value": {"type": "string"}},
        "type": "object",
        "properties": {
            "value": {"$ref": "#/$defs/value", "type": "string"},
        },
    }
    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        _negotiate_google(schema)
    assert exc.value.issues[0]["path"] == "/properties/value/type"


def test_google_response_json_schema_allows_dollar_prefixed_ref_siblings():
    schema = {
        "$defs": {"value": {"type": "string"}},
        "type": "object",
        "properties": {
            "value": {"$ref": "#/$defs/value", "$anchor": "valueReference"},
        },
    }
    assert _negotiate_google(schema).provider_schema == schema


@pytest.mark.parametrize("required", [False, True])
def test_google_response_json_schema_rejects_only_required_reference_cycles(required):
    node = {
        "type": "object",
        "properties": {"next": {"$ref": "#/$defs/node"}},
    }
    if required:
        node["required"] = ["next"]
    schema = {
        "$defs": {"node": node},
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/node"}},
        "required": ["root"],
    }
    if required:
        with pytest.raises(StructuredOutputUnsupportedError) as exc:
            _negotiate_google(schema)
        assert exc.value.issues[0]["path"] == "/required/0"
    else:
        assert _negotiate_google(schema).provider_schema == schema


@pytest.mark.parametrize("reference_style", ["pointer", "anchor"])
@pytest.mark.parametrize("required", [False, True])
def test_google_required_property_rejects_reachable_cycle_outside_root(
    reference_style,
    required,
):
    if reference_style == "pointer":
        definitions = {
            "a": {"$ref": "#/$defs/b"},
            "b": {"$ref": "#/$defs/a"},
        }
        root_ref = "#/$defs/a"
    else:
        definitions = {
            "a": {"$anchor": "a", "$ref": "#b"},
            "b": {"$anchor": "b", "$ref": "#a"},
        }
        root_ref = "#a"
    schema = {
        "$defs": definitions,
        "type": "object",
        "properties": {"root": {"$ref": root_ref}},
    }
    if required:
        schema["required"] = ["root"]
    with pytest.raises(StructuredOutputSchemaError) as exc:
        _negotiate_google(schema)
    assert exc.value.code == "invalid_schema"


def test_google_required_property_accepts_acyclic_local_ref_chain():
    schema = {
        "$defs": {
            "a": {"$ref": "#/$defs/b"},
            "b": {"type": "string"},
        },
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/a"}},
        "required": ["root"],
    }
    assert _negotiate_google(schema).provider_schema == schema


def test_google_required_reachable_cycle_fails_before_credentials_and_network():
    schema = {
        "$defs": {
            "a": {"$ref": "#/$defs/b"},
            "b": {"$ref": "#/$defs/a"},
        },
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/a"}},
        "required": ["root"],
    }
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
                model=_google_model(),
                response_format=normalize_response_format({
                    "type": "json_schema",
                    "schema": schema,
                    "fallback": "none",
                }),
                get_api_key=lambda provider: credentials.append(provider),
                convert_to_llm=lambda messages: messages,
            ),
            stream_fn=stream_fn,
        )
        return await stream.result()

    with pytest.raises(StructuredOutputSchemaError) as exc:
        asyncio.run(run())
    assert exc.value.code == "invalid_schema"
    assert credentials == []
    assert provider_calls == []


@pytest.mark.parametrize("reference_style", ["pointer", "anchor"])
def test_google_required_outer_accepts_referenced_optional_recursive_property(
    reference_style,
):
    node = {
        "type": "object",
        "properties": {
            "next": {"$ref": "#/$defs/node" if reference_style == "pointer" else "#node"},
        },
    }
    if reference_style == "anchor":
        node["$anchor"] = "node"
    schema = {
        "$defs": {"node": node},
        "type": "object",
        "properties": {
            "root": {
                "$ref": "#/$defs/node" if reference_style == "pointer" else "#node",
            },
        },
        "required": ["root"],
    }
    assert _negotiate_google(schema).provider_schema == schema


@pytest.mark.parametrize("reference_style", ["pointer", "anchor"])
def test_google_required_child_rejects_unreferenced_cyclic_definitions(
    reference_style,
):
    if reference_style == "pointer":
        definitions = {
            "a": {"$ref": "#/properties/root/$defs/b"},
            "b": {"$ref": "#/properties/root/$defs/a"},
        }
    else:
        definitions = {
            "a": {"$anchor": "a", "$ref": "#b"},
            "b": {"$anchor": "b", "$ref": "#a"},
        }
    schema = {
        "type": "object",
        "properties": {
            "root": {
                "$defs": definitions,
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        },
        "required": ["root"],
    }
    with pytest.raises(StructuredOutputSchemaError) as exc:
        _negotiate_google(schema)
    assert exc.value.code == "invalid_schema"


@pytest.mark.parametrize("reference_style", ["pointer", "anchor"])
def test_google_required_outer_accepts_cycle_with_optional_object_edge(reference_style):
    if reference_style == "pointer":
        ref_a = "#/$defs/a"
        ref_b = "#/$defs/b"
        anchors = ({}, {})
    else:
        ref_a = "#a"
        ref_b = "#b"
        anchors = ({"$anchor": "a"}, {"$anchor": "b"})
    schema = {
        "$defs": {
            "a": {
                **anchors[0],
                "type": "object",
                "properties": {"b": {"$ref": ref_b}},
                "required": ["b"],
            },
            "b": {
                **anchors[1],
                "type": "object",
                "properties": {"a": {"$ref": ref_a}},
            },
        },
        "type": "object",
        "properties": {"root": {"$ref": ref_a}},
        "required": ["root"],
    }
    assert _negotiate_google(schema).provider_schema == schema


def test_google_shared_schema_dag_preflight_is_linear_time():
    definitions = {"leaf": {"type": "string"}}
    target = "leaf"
    for depth in reversed(range(22)):
        name = f"n{depth}"
        definitions[name] = {
            "anyOf": [
                {"$ref": f"#/$defs/{target}"},
                {"$ref": f"#/$defs/{target}"},
            ],
        }
        target = name
    schema = {"$defs": definitions, "$ref": "#/$defs/n0"}
    assert len(json.dumps(schema, separators=(",", ":"))) < 2000

    started = time.perf_counter()
    assert _negotiate_google(schema).provider_schema == schema
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("limit", ["depth", "nodes", "edges"])
def test_google_schema_graph_limits_fail_closed_with_bounded_issue(monkeypatch, limit):
    import openprogram.providers.structured_output as structured_output

    if limit == "depth":
        monkeypatch.setattr(structured_output, "_GOOGLE_SCHEMA_MAX_DEPTH", 3)
        schema = {"type": "string"}
        for _ in range(4):
            schema = {"type": "array", "items": schema}
    elif limit == "nodes":
        monkeypatch.setattr(structured_output, "_GOOGLE_SCHEMA_MAX_NODES", 3)
        schema = {"$defs": {name: {"type": "string"} for name in ("a", "b", "c")}}
    else:
        monkeypatch.setattr(structured_output, "_GOOGLE_SCHEMA_MAX_EDGES", 1)
        schema = {"anyOf": [{"type": "string"}, {"type": "number"}]}

    if limit == "depth":
        with pytest.raises(StructuredOutputSchemaError) as exc:
            _negotiate_google(schema)
        assert str(exc.value) == "JSON Schema exceeds depth limit"
        return

    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        _negotiate_google(schema)
    issue = exc.value.issues[0]
    assert issue["message"] == f"Google response_json_schema exceeds {limit} limit"
    assert len(issue["path"]) <= 512


def test_google_schema_depth_at_configured_limit_is_accepted(monkeypatch):
    import openprogram.providers.structured_output as structured_output

    monkeypatch.setattr(structured_output, "_GOOGLE_SCHEMA_MAX_DEPTH", 8)
    schema = {"type": "string"}
    for _ in range(8):
        schema = {"type": "array", "items": schema}
    assert _negotiate_google(schema).provider_schema == schema


@pytest.mark.parametrize("depth", [127, 128, 129])
def test_google_public_schema_depth_never_leaks_recursion_error(depth):
    with pytest.raises(StructuredOutputSchemaError) as exc:
        _negotiate_google(_nested_array_schema(depth))
    assert exc.value.code == "invalid_schema"


def test_google_public_schema_depth_boundary_matches_configured_limit():
    import openprogram.providers.structured_output as structured_output

    assert structured_output._GOOGLE_SCHEMA_MAX_DEPTH == 100
    assert _negotiate_google(_nested_array_schema(100)).mode == "native"
    with pytest.raises(StructuredOutputSchemaError):
        _negotiate_google(_nested_array_schema(101))


def test_google_long_unsupported_keyword_path_uses_shared_bound():
    property_name = "x" * 1000
    schema = {
        "type": "object",
        "properties": {
            property_name: {"type": "string", "pattern": "^x$"},
        },
    }
    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        _negotiate_google(schema)
    issue = exc.value.issues[0]
    assert len(issue["path"]) <= 512
    assert issue["schema_path"] == issue["path"]


def test_google_percent_decodes_fragment_before_json_pointer_resolution():
    schema = {
        "$defs": {
            "a b": {
                "type": "object",
                "properties": {"next": {"$ref": "#/$defs/a%20b"}},
                "required": ["next"],
            },
        },
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/a%20b"}},
        "required": ["root"],
    }
    with pytest.raises(StructuredOutputUnsupportedError) as exc:
        _negotiate_google(schema)
    assert exc.value.issues[0]["path"] == "/required/0"


@pytest.mark.parametrize("resource", ["child-a", "child-b"])
def test_google_anchor_resolution_is_scoped_by_nested_id(resource):
    schema = {
        "$id": "https://schemas.example/root",
        "$defs": {
            "a": {
                "$id": "child-a",
                "$anchor": "x",
                "type": "object",
                "properties": {"next": {"$ref": "#x"}},
                "required": ["next"],
            },
            "b": {"$id": "child-b", "$anchor": "x", "type": "string"},
        },
        "type": "object",
        "properties": {"root": {"$ref": f"{resource}#x"}},
        "required": ["root"],
    }
    if resource == "child-a":
        with pytest.raises(StructuredOutputUnsupportedError) as exc:
            _negotiate_google(schema)
        assert exc.value.issues[0]["path"] == "/required/0"
    else:
        assert _negotiate_google(schema).provider_schema == schema


def test_google_unresolved_external_resource_fails_closed_before_negotiation():
    schema = {
        "type": "object",
        "properties": {"root": {"$ref": "https://external.example/schema#x"}},
        "required": ["root"],
    }
    with pytest.raises(StructuredOutputSchemaError) as exc:
        _negotiate_google(schema)
    assert exc.value.code == "invalid_schema"


@pytest.mark.parametrize(
    ("finish_reason", "prompt_block", "expected_reason", "event_type"),
    [
        ("SAFETY", None, "error", EventError),
        ("MAX_TOKENS", None, "length", EventDone),
        ("STOP", "SAFETY", "error", EventError),
    ],
)
def test_google_terminal_reason_precedes_mixed_function_call(
    monkeypatch,
    finish_reason,
    prompt_block,
    expected_reason,
    event_type,
):
    from google import genai
    from google.genai import types as gtypes

    response = gtypes.GenerateContentResponse(
        candidates=[gtypes.Candidate(
            finish_reason=getattr(gtypes.FinishReason, finish_reason),
            content=gtypes.Content(parts=[gtypes.Part(
                function_call=gtypes.FunctionCall(name="lookup", args={"key": "x"}),
            )]),
        )],
        prompt_feedback=(
            gtypes.GenerateContentResponsePromptFeedback(
                block_reason=getattr(gtypes.BlockedReason, prompt_block),
            )
            if prompt_block else None
        ),
    )

    class Models:
        async def generate_content_stream(self, **kwargs):
            async def chunks():
                yield response
            return chunks()

    class Client:
        def __init__(self, **kwargs):
            self.aio = type("Aio", (), {"models": Models()})()

    monkeypatch.setattr(genai, "Client", Client)

    async def consume():
        return [event async for event in google.stream_simple(
            _google_model(),
            Context(),
            SimpleStreamOptions(api_key="test"),
        )]

    events = asyncio.run(consume())
    assert isinstance(events[-1], event_type)
    assert events[-1].reason == expected_reason
    assert events[-1].reason != "toolUse"

    from openprogram.agent.types import AgentTool, AgentToolResult

    executed = []

    async def execute(call_id, args, cancel, on_update):
        executed.append((call_id, args))
        return AgentToolResult(content=[])

    async def run_loop():
        stream = agent_loop(
            [UserMessage(content="lookup", timestamp=1)],
            AgentContext(tools=[AgentTool(
                name="lookup",
                description="Lookup",
                parameters={"type": "object", "properties": {}},
                label="lookup",
                execute=execute,
            )]),
            AgentLoopConfig(
                model=_google_model(),
                get_api_key=lambda provider: "test",
                convert_to_llm=lambda messages: messages,
            ),
            stream_fn=google.stream_simple,
        )
        try:
            await stream.result()
        except Exception:
            pass

    asyncio.run(run_loop())
    assert executed == []


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
