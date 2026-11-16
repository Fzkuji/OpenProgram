from __future__ import annotations

import copy

from openprogram.models.providers.shared.openai_responses import convert_responses_tools
from openprogram.models.providers.types import Tool


def test_codex_tool_schema_drops_unsupported_all_of_without_mutating_tool() -> None:
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["observe", "verify"]},
            "value": {"type": "string"},
        },
        "required": ["action"],
        "allOf": [{
            "if": {
                "properties": {"action": {"const": "verify"}},
                "required": ["action"],
            },
            "then": {"required": ["value"]},
        }],
        "additionalProperties": False,
    }
    original = copy.deepcopy(parameters)

    converted = convert_responses_tools(
        [Tool(name="page_tool", description="Browser action", parameters=parameters)],
        "openai-codex",
        "gpt-5.6-luna",
    )

    assert converted[0]["strict"] is True
    assert "allOf" not in converted[0]["parameters"]
    assert converted[0]["parameters"]["required"] == ["action", "value"]
    assert converted[0]["parameters"]["properties"]["value"]["type"] == [
        "string",
        "null",
    ]
    assert parameters == original


def test_strict_optional_enum_accepts_null() -> None:
    """A nullable type is not enough: the enum must list null too, or the
    model is forced to pick a member for a parameter it meant to omit."""
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "space": {"type": "string", "enum": ["self", "legacy_global"]},
            "mode": {"enum": ["a", "b"]},
            "kind": {"type": "string", "enum": ["x", "y"]},
        },
        "required": ["query", "kind"],
    }
    original = copy.deepcopy(parameters)

    converted = convert_responses_tools(
        [Tool(name="memory_search", description="Search", parameters=parameters)],
        "openai-codex",
        "gpt-5.6-luna",
    )[0]["parameters"]["properties"]

    assert converted["space"]["type"] == ["string", "null"]
    assert converted["space"]["enum"] == ["self", "legacy_global", None]
    assert converted["mode"]["enum"] == ["a", "b", None]
    # A required enum stays exact.
    assert converted["kind"]["enum"] == ["x", "y"]
    assert parameters == original
