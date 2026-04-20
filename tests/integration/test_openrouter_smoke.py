"""Smoke test: Runtime's AgentSession path against an OpenRouter free model.

Exercises the two shapes of exec() that the new provider pathway must handle:
  1. plain chat (no tools) — builds one user message, streams assistant back
  2. chat + tool loop  — AgentSession runs the agent_loop, calls the supplied
     Python tool executor, and the final assistant message becomes the return

Requires OPENROUTER_API_KEY in the environment. Skipped otherwise.
"""
from __future__ import annotations

import os

import pytest

from openprogram.agentic_programming.runtime import Runtime


OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
skip_no_key = pytest.mark.skipif(
    not OPENROUTER_KEY, reason="OPENROUTER_API_KEY not set"
)

RUNTIME_MODEL = "openrouter:openai/gpt-oss-120b:free"


@skip_no_key
def test_chat_no_tool():
    rt = Runtime(model=RUNTIME_MODEL, api_key=OPENROUTER_KEY)
    try:
        reply = rt.exec([{"type": "text", "text": "Reply with exactly the word: PONG"}])
    finally:
        rt.close()

    assert isinstance(reply, str)
    assert reply.strip()
    assert "pong" in reply.lower()


@skip_no_key
def test_chat_with_tool():
    calls: list[dict] = []

    def add(a: int, b: int) -> str:
        calls.append({"a": a, "b": b})
        return str(int(a) + int(b))

    tool = {
        "spec": {
            "name": "add",
            "description": "Add two integers and return the sum as a string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        },
        "execute": add,
    }

    rt = Runtime(model=RUNTIME_MODEL, api_key=OPENROUTER_KEY)
    try:
        reply = rt.exec(
            [{"type": "text", "text": "What is 17 + 25? Use the add tool, then answer."}],
            tools=[tool],
        )
    finally:
        rt.close()

    assert calls, f"tool was never invoked; reply={reply!r}"
    assert any(c == {"a": 17, "b": 25} for c in calls), f"unexpected args: {calls}"
    assert "42" in reply
