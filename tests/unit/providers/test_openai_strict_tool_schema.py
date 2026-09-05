from __future__ import annotations

import copy

from openprogram.providers._shared.openai_responses import convert_responses_tools
from openprogram.providers.types import Tool


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
        [Tool(name="web_use", description="Browser action", parameters=parameters)],
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
