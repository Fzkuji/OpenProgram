"""Smoke test: Runtime's AgentSession path against an OpenRouter free model.

Exercises the two shapes of exec() that the new provider pathway must handle:
  1. plain chat (no tools) — builds one user message, streams assistant back
  2. chat + tool loop  — AgentSession runs the agent_loop, calls the supplied
     Python tool executor, and the final assistant message becomes the return

Requires OPENROUTER_API_KEY in the environment. Skipped otherwise.
Marked ``slow``: it spends real API quota and needs real network, so it
only runs when selected explicitly (``pytest -m slow``).
"""
from __future__ import annotations

import os

import pytest

from openprogram.agentic_programming.runtime import Runtime

pytestmark = pytest.mark.slow

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
skip_no_key = pytest.mark.skipif(
    not OPENROUTER_KEY, reason="OPENROUTER_API_KEY not set"
)


@pytest.fixture
def runtime_model() -> str:
    """First :free model the catalog still resolves — free-tier ids churn,
    so a hardcoded one rots into 'Unknown model'."""
    from openprogram.providers import get_model
    candidates = (
        "openai/gpt-oss-120b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-chat-v3-0324:free",
    )
    for mid in candidates:
        if get_model("openrouter", mid) is not None:
            return f"openrouter:{mid}"
    pytest.skip("no known :free openrouter model in the catalog")


@skip_no_key
def test_chat_no_tool(runtime_model):
    rt = Runtime(model=runtime_model, api_key=OPENROUTER_KEY)
    try:
        reply = rt.exec([{"type": "text", "text": "Reply with exactly the word: PONG"}])
    finally:
        rt.close()

    assert isinstance(reply, str)
    assert reply.strip()
    assert "pong" in reply.lower()


@skip_no_key
@pytest.mark.xfail(
    reason="live free model (gpt-oss-120b:free) intermittently emits the "
    "tool call as plain text instead of a structured tool_call, so the "
    "loop never executes the tool. Model-behavior flakiness, not a wiring "
    "bug — the no-tool smoke and the unit-level tool-loop tests cover the "
    "code path deterministically.",
    strict=False,
)
def test_chat_with_tool(runtime_model):
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

    rt = Runtime(model=runtime_model, api_key=OPENROUTER_KEY)
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
