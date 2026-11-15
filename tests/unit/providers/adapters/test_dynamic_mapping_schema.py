"""Dynamic keys survive canonical schemas and each production provider converter."""
import copy
from collections.abc import Mapping

import pytest

from openprogram.programs._host.runtime import _close_objects, _python_type_to_json_schema, _widen_optionals_to_null
from openprogram.models.providers.schema import SchemaNormalizationError, normalize, normalize_for
from openprogram.models.providers.shared.openai_responses import convert_responses_tools
from openprogram.models.providers.types import Context, Model, Tool, ToolCall
from openprogram.models.providers.utils.validation import validate_tool_arguments


@pytest.mark.parametrize("annotation", [dict, dict[str, int], Mapping[str, int], dict[str, int | None], Mapping[str, int | None]])
def test_mapping_annotations_keep_value_constraint_and_nullable(annotation):
    value = _python_type_to_json_schema(annotation)
    expected = (True if annotation is dict else
                {"type": ["integer", "null"]} if annotation in (dict[str, int | None], Mapping[str, int | None])
                else {"type": "integer"})
    assert value == {"type": "object", "additionalProperties": expected}
    canonical = _widen_optionals_to_null({"type": "object", "properties": {"value": value}})
    assert "additionalProperties" not in canonical
    tool = Tool(name="mapped", description="Mapping", parameters=canonical)
    original = copy.deepcopy(canonical)
    assert validate_tool_arguments(tool, ToolCall(id="ok", name="mapped", arguments={"value": {"task": 7}})) == {"value": {"task": 7}}
    assert validate_tool_arguments(tool, ToolCall(id="null", name="mapped", arguments={"value": None})) == {"value": None}
    with pytest.raises(ValueError):
        validate_tool_arguments(tool, ToolCall(id="bad", name="mapped", arguments={"value": [7]}))
    if annotation is not dict:
        with pytest.raises(ValueError):
            validate_tool_arguments(tool, ToolCall(id="bad", name="mapped", arguments={"value": {"task": {"wrong": 7}}}))
    if annotation in (dict[str, int | None], Mapping[str, int | None]):
        assert validate_tool_arguments(tool, ToolCall(id="nullable-value", name="mapped", arguments={"value": {"task": None}})) == {"value": {"task": None}}
    assert canonical == original


@pytest.mark.parametrize("fixed", [{"type": "object", "properties": {}}, {"type": "object", "additionalProperties": False}, {"type": "object", "properties": {"task": {"type": "string"}}}])
def test_explicit_fixed_objects_remain_closed(fixed):
    fixed = _close_objects(fixed)
    assert fixed["additionalProperties"] is False
    tool = Tool(name="fixed", description="Fixed", parameters=fixed)
    with pytest.raises(ValueError, match="unexpected"):
        validate_tool_arguments(tool, ToolCall(id="bad", name="fixed", arguments={"unexpected": 7}))
    assert convert_responses_tools([tool], "openai-codex", "owned")[0]["strict"] is True


@pytest.mark.parametrize("nested", [{"type": "array", "items": {"type": "object"}}, {"anyOf": [{"type": "object", "additionalProperties": {"type": "integer"}}, {"type": "null"}]}])
def test_nested_mapping_is_never_silently_closed(nested):
    canonical = _widen_optionals_to_null({"type": "object", "properties": {"payload": nested}, "required": ["payload"]})
    assert normalize_for("openai-codex", canonical, "owned") == canonical
    with pytest.raises(SchemaNormalizationError, match="dynamic mapping"):
        normalize(canonical, "openai_strict")


@pytest.mark.parametrize("api", ["openai-codex", "openai-responses", "azure-openai-responses", "openai-completions", "anthropic-messages"])
def test_provider_mixed_tools_choose_strict_per_schema(api, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_STRICT_TOOLS", "1")
    mapping = _widen_optionals_to_null({"type": "object", "properties": {"args": {"type": "object", "additionalProperties": {"type": "integer"}}}, "required": ["args"]})
    fixed = {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"], "additionalProperties": False}
    tools = [Tool(name="mapping", description="Mapping", parameters=mapping), Tool(name="fixed", description="Fixed", parameters=fixed)]
    originals = copy.deepcopy([mapping, fixed])
    model = Model(id="claude-sonnet-4-5", name="Owned", api=api, provider="owned", base_url="https://example.invalid")
    if api == "openai-completions":
        from openprogram.models.providers.adapters.openai_completions.openai_completions import _build_tools
        converted = [t["function"] for t in _build_tools(Context(tools=tools), model)]
    elif api == "anthropic-messages":
        from openprogram.models.providers.adapters.anthropic.anthropic import _build_tools
        converted = _build_tools(Context(tools=tools), model=model, strict=True)
    else:
        converted = convert_responses_tools(tools, api, model.id)
    assert converted[0].get("strict", False) is False
    assert converted[1]["strict"] is True
    key = "input_schema" if api == "anthropic-messages" else "parameters"
    assert converted[0][key] == mapping
    assert [mapping, fixed] == originals
    with pytest.raises(ValueError):
        validate_tool_arguments(tools[0], ToolCall(id="bad", name="mapping", arguments={"args": {"task": {"wrong": 1}}}))
    with pytest.raises(ValueError, match="unexpected"):
        validate_tool_arguments(tools[1], ToolCall(id="bad", name="fixed", arguments={"code": "pass", "unexpected": 1}))


def test_example_data_does_not_disable_fixed_tool_strict():
    schema = {"type": "object", "properties": {}, "examples": [{"type": "object", "additionalProperties": True}]}
    tool = Tool(name="fixed", description="Fixed", parameters=schema)
    assert convert_responses_tools([tool], "openai-codex", "owned")[0]["strict"] is True


@pytest.mark.parametrize("annotation,values", [
    (dict[str, list[int | None]], {"array": [7, None]}),
    (Mapping[str, list[int | None]], {"array": [7, None]}),
    (dict[str, dict[str, int | None]], {"nested": {"nullable": None, "integer": 7}}),
    (Mapping[str, Mapping[str, int | None]], {"nested": {"nullable": None, "integer": 7}}),
])
def test_mapping_value_nullable_constraints_survive_nested_collections(annotation, values):
    canonical = _widen_optionals_to_null({"type": "object", "properties": {"values": _python_type_to_json_schema(annotation)}, "required": ["values"]})
    tool = Tool(name="nested_mapping", description="Nested mapping", parameters=canonical)
    assert validate_tool_arguments(tool, ToolCall(id="owned-good", name=tool.name, arguments={"values": values})) == {"values": values}
    assert normalize_for("openai-codex", canonical, "owned") == canonical
    with pytest.raises(ValueError):
        validate_tool_arguments(tool, ToolCall(id="owned-bad", name=tool.name, arguments={"values": {"wrong": {"unexpected": [None]}}}))
