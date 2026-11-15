"""Strict function-tool wire arguments retain canonical Python defaults."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

from jsonschema import Draft7Validator
import pytest

from openprogram.models.providers.types import Context, Tool, ToolCall
from openprogram.models.providers.utils.validation import validate_tool_call


def _wire(tool):
    from openprogram.models.providers.adapters.openai_codex.openai_codex import _build_request_body
    return _build_request_body(SimpleNamespace(id="fixture", api="openai-codex"),
                               Context(tools=[tool]), {}, [])["tools"][0]


def _validate(schema, args):
    original_schema, original_args = copy.deepcopy(schema), copy.deepcopy(args)
    tool = Tool(name="fixture", description="Fixture", parameters=schema)
    wire = _wire(tool)
    assert wire["strict"] is True
    Draft7Validator(wire["parameters"]).validate(args)
    result = validate_tool_call([tool], ToolCall(id="fixture", name=tool.name, arguments=args))
    assert schema == original_schema and args == original_args
    return result


def test_raw_memory_get_compose_adapter_restores_omitted_sections():
    from openprogram.runtime.agent.runtime.shared import _adapt_tools
    from openprogram.programs.tools.knowledge.memory.memory import GET_SPEC, memory_get
    adapted, = _adapt_tools([{"spec": GET_SPEC, "execute": memory_get}])
    args = {"path": "sources/fixture.md", "heading": None, "block_id": None, "space": "self"}
    assert _validate(adapted.parameters, args) == {"path": "sources/fixture.md", "space": "self"}


def test_registered_memory_get_native_nullable_sections_remain_none():
    from openprogram.programs import get_agent_tool
    registered = get_agent_tool("memory_get")
    args = {"path": "sources/fixture.md", "heading": None, "block_id": None, "space": "self"}
    assert _validate(registered.parameters, args) == args


@pytest.mark.parametrize("kind", ["string", "boolean", "integer", "object", "array"])
def test_optional_null_uses_default_and_required_null_is_invalid(kind):
    value = {"type": kind}
    if kind == "object":
        value["properties"] = {}  # Fixed object; anonymous objects are dynamic mappings.
    schema = {"type": "object", "properties": {"value": value}}
    assert _validate(schema, {"value": None}) == {}
    schema["required"] = ["value"]
    tool = Tool(name="fixture", description="Fixture", parameters=schema)
    with pytest.raises(ValueError, match="value"):
        validate_tool_call([tool], ToolCall(id="fixture", name="fixture", arguments={"value": None}))


@pytest.mark.parametrize("prop", [{"type": ["string", "null"]},
                                  {"anyOf": [{"type": "string"}, {"type": "null"}]}])
@pytest.mark.parametrize("required", [False, True])
def test_native_nullable_values_are_not_omitted(prop, required):
    schema = {"type": "object", "properties": {"value": prop}}
    if required:
        schema["required"] = ["value"]
    assert _validate(schema, {"value": None}) == {"value": None}


def test_optional_strict_enum_null_means_omitted():
    # Strict mode makes every property required, so an optional enum needs
    # null to say "omitted"; without it the model must pick a value.
    schema = {"type": "object", "properties": {"space": {"type": "string", "enum": ["self"]}}}
    tool = Tool(name="fixture", description="Fixture", parameters=schema)
    args = {"space": None}
    assert Draft7Validator(_wire(tool)["parameters"]).is_valid(args)
    assert validate_tool_call([tool], ToolCall(id="fixture", name="fixture", arguments=args)) == {}


def test_widening_an_optional_enum_leaves_a_shared_enum_list_alone():
    # web_search names its providers once: the optional provider and the
    # providers items share one list. Null belongs to the provider only; a
    # string-typed item enum holding null makes Codex fail every request.
    names = ["tavily", "exa"]
    schema = {"type": "object", "properties": {
        "provider": {"type": "string", "enum": names},
        "providers": {"type": "array", "items": {"type": "string", "enum": names}},
    }}
    wire = _wire(Tool(name="fixture", description="Fixture", parameters=schema))["parameters"]
    assert None in wire["properties"]["provider"]["enum"]
    assert wire["properties"]["providers"]["items"]["enum"] == ["tavily", "exa"]
    assert names == ["tavily", "exa"]


def test_null_not_allowed_by_required_enum_is_still_invalid():
    schema = {"type": "object", "properties": {"space": {"type": "string", "enum": ["self"]}},
              "required": ["space"]}
    tool = Tool(name="fixture", description="Fixture", parameters=schema)
    args = {"space": None}
    assert not Draft7Validator(_wire(tool)["parameters"]).is_valid(args)
    with pytest.raises(ValueError, match="space"):
        validate_tool_call([tool], ToolCall(id="fixture", name="fixture", arguments=args))


def test_unknown_null_property_is_not_silently_discarded():
    tool = Tool(name="fixture", description="Fixture", parameters={"type": "object", "properties": {}, "additionalProperties": False})
    with pytest.raises(ValueError, match="unexpected"):
        validate_tool_call([tool], ToolCall(id="fixture", name="fixture", arguments={"unexpected": None}))


def test_nested_array_objects_decode_defaults_without_removing_array_positions():
    item = {"type": "object", "properties": {"hint": {"type": "string"},
            "keep": {"type": ["boolean", "null"]}}}
    schema = {"type": "object", "properties": {"rows": {"type": "array", "items": item}}, "required": ["rows"]}
    assert _validate(schema, {"rows": [{"hint": None, "keep": None}]}) == {"rows": [{"keep": None}]}
    tool = Tool(name="fixture", description="Fixture", parameters=schema)
    with pytest.raises(ValueError, match="rows.0"):
        validate_tool_call([tool], ToolCall(id="fixture", name="fixture", arguments={"rows": [None]}))
    item["type"] = ["object", "null"]
    # Native nullable item positions remain present.
    assert validate_tool_call([Tool(name="fixture", description="Fixture", parameters=schema)],
                             ToolCall(id="fixture", name="fixture", arguments={"rows": [None]})) == {"rows": [None]}


@pytest.mark.parametrize("combinator", ["anyOf", "oneOf"])
def test_union_branch_optional_nulls_retain_discriminator_and_branch_constraints(combinator):
    branches = [{"type": "object", "properties": {"mode": {"type": "string", "enum": [mode]},
                 "hint": {"type": "string"}}, "required": ["mode"]} for mode in ("a", "b")]
    schema = {"type": "object", "properties": {"payload": {combinator: branches}}, "required": ["payload"]}
    assert _validate(schema, {"payload": {"mode": "a", "hint": None}}) == {"payload": {"mode": "a"}}
    tool = Tool(name="fixture", description="Fixture", parameters=schema)
    with pytest.raises(ValueError, match="payload"):
        validate_tool_call([tool], ToolCall(id="fixture", name="fixture", arguments={"payload": {"mode": "c", "hint": None}}))


def test_conditional_required_constraint_survives_strict_null_decoding():
    schema = {"type": "object", "properties": {"action": {"type": "string"}, "value": {"type": "string"}},
              "required": ["action"], "allOf": [{"if": {"properties": {"action": {"const": "verify"}}},
                                                "then": {"required": ["value"]}}]}
    assert _validate(schema, {"action": "observe", "value": None}) == {"action": "observe"}
    with pytest.raises(ValueError, match="value"):
        _validate(schema, {"action": "verify", "value": None})


def test_array_union_decodes_nested_optional_fields():
    schema = {"type": "object", "properties": {"payload": {"anyOf": [
        {"type": "array", "items": {"type": "object", "properties": {"hint": {"type": "string"}}}},
        {"type": "string"},
    ]}}, "required": ["payload"]}
    assert _validate(schema, {"payload": [{"hint": None}]}) == {"payload": [{}]}


@pytest.mark.parametrize("strict_enabled", ["1", "0"])
def test_public_agent_tool_executes_unchanged_python_default(monkeypatch, strict_enabled):
    from openprogram.runtime.agent.runtime.shared import _adapt_tools
    monkeypatch.setenv("OPENPROGRAM_STRICT_TOOLS", strict_enabled)
    calls = []

    def fixture(value="original default"):
        calls.append(value)
        return value

    adapted, = _adapt_tools([{"spec": {"name": "fixture_default", "parameters": {
        "type": "object", "properties": {"value": {"type": "string"}}}}, "execute": fixture}])
    tool = Tool(name=adapted.name, description=adapted.description, parameters=adapted.parameters)
    for args in ({"value": None}, {}):
        validated = validate_tool_call([tool], ToolCall(id="fixture", name=adapted.name, arguments=args))
        result = asyncio.run(adapted.execute("fixture", validated, None, None))
        assert not result.is_error
        assert "original default" in "".join(content.text for content in result.content)
    assert calls == ["original default", "original default"]
    # Native nullable arguments continue to reach the Python body as None.
    tool.parameters["properties"]["value"]["type"] = ["string", "null"]
    validated = validate_tool_call([tool], ToolCall(id="nullable", name=adapted.name, arguments={"value": None}))
    result = asyncio.run(adapted.execute("nullable", validated, None, None))
    assert not result.is_error
    assert calls[-1] is None


def test_basic_fallback_restores_optional_null_and_rejects_required_null(monkeypatch):
    from openprogram.models.providers.utils import validation
    monkeypatch.setattr(validation, "JSONSCHEMA_AVAILABLE", False)
    schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    assert _validate(schema, {"value": None}) == {}
    schema["required"] = ["value"]
    with pytest.raises(ValueError, match="value"):
        validate_tool_call([Tool(name="fixture", description="Fixture", parameters=schema)],
                           ToolCall(id="fixture", name="fixture", arguments={"value": None}))


@pytest.mark.parametrize("basic_fallback", [False, True])
def test_passthrough_only_boolean_property_schema_retains_native_null(monkeypatch, basic_fallback):
    from openprogram.models.providers.utils import validation
    if basic_fallback:
        monkeypatch.setattr(validation, "JSONSCHEMA_AVAILABLE", False)
    tool = Tool(name="fixture", description="Fixture", parameters={"type": "object", "properties": {"free": True}})
    args = {"free": None}
    assert validate_tool_call([tool], ToolCall(id="fixture", name="fixture", arguments=args)) == args


@pytest.mark.parametrize("constraint", [
    {"not": {"type": "null"}},
    {"if": {"type": "null"}, "then": False},
    {"if": {"type": "string"}, "then": {"minLength": 1}, "else": False},
])
def test_basic_fallback_does_not_remove_null_when_wire_acceptance_is_unknown(monkeypatch, constraint):
    from openprogram.models.providers.utils import validation
    schema = {"type": "object", "properties": {"value": {"type": "string", **constraint}}}
    tool = Tool(name="fixture", description="Fixture", parameters=schema)
    call = ToolCall(id="fixture", name="fixture", arguments={"value": None})
    assert not Draft7Validator(_wire(tool)["parameters"]).is_valid(call.arguments)
    with pytest.raises(ValueError, match="value"):
        validate_tool_call([tool], call)
    monkeypatch.setattr(validation, "JSONSCHEMA_AVAILABLE", False)
    # The existing fallback remains lightweight, but must not reinterpret an
    # unsupported null constraint as an omitted/default argument.
    assert validate_tool_call([tool], call) == {"value": None}


def test_a_null_for_a_typed_default_reaches_the_function_as_its_default():
    # worktree_create(pr: str = "") once received None and failed on .strip().
    from openprogram.programs._host.runtime import function

    @function(name="null_default_fixture", register_globally=False)
    def fixture(pr: str = "", count: int = 3, keep: str | None = "kept") -> str:
        """Fixture.

        Args:
            pr: A string with a default.
            count: An integer with a default.
            keep: A nullable string.
        """
        return repr((pr, count, keep))

    result = asyncio.run(fixture.execute("c", {"pr": None, "count": None, "keep": None}, None, None))
    assert result.content[0].text == "('', 3, None)"
