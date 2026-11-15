"""Opt-in Grok subscription tool execution with prompt-based schema validation."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


def test_selected_grok_model_executes_native_tool_before_structured_reply():
    if os.environ.get("OPENPROGRAM_TEST_LIVE") != "1":
        pytest.skip("requires an explicitly enabled authenticated Grok live test")
    from openprogram.runtime.agent.runtime.factory import create_runtime

    calls = []

    def lookup_report(member: str) -> str:
        calls.append(member)
        return '{"member":"Alice","marker":"receipt-42"}'

    tool = {
        "spec": {
            "name": "lookup_report",
            "description": "Read a synthetic report fixture.",
            "parameters": {
                "type": "object", "properties": {"member": {"type": "string"}},
                "required": ["member"], "additionalProperties": False,
            },
        },
        "execute": lookup_report,
    }
    # Optional/free-object fields deliberately exercise the report's prompt
    # fallback rather than a different native strict-schema request.
    schema = {
        "type": "object",
        "properties": {
            "member": {"type": "string"}, "marker": {"type": "string"},
            "notes": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["member", "marker"], "additionalProperties": False,
    }
    runtime = create_runtime(provider="xai-subscription", model="grok-4.6")
    try:
        result = runtime.exec(
            [{"type": "text", "text": (
                "Call lookup_report for Alice exactly once. Then output one JSON "
                "object with member and the exact marker obtained from the actual "
                "tool result. Never invent a result."
            )}],
            tools=[tool], max_iterations=None, timeout_s=120,
            response_format={"type": "json_schema", "schema": schema, "fallback": "prompt"},
        )
        assert calls == ["Alice"]
        assert result["member"] == "Alice"
        assert result["marker"] == "receipt-42"
    finally:
        runtime.close()
