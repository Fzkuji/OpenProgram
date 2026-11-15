"""No request carries server-side context edits (context/compaction.html,
"What is not done"): old tool results leave the context only through
compaction, as in Pi, Oh My Pi and Codex."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from openprogram.models.providers.adapters.anthropic import anthropic
from openprogram.models.providers.types import Context, Model, SimpleStreamOptions, ToolResultMessage, TextContent


class _Stream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self):
        return NS(usage=NS(input_tokens=1, output_tokens=1), stop_reason="end_turn")


@pytest.mark.parametrize("model_id", ["claude-opus-4-6[1m]", "claude-opus-4-6"])
def test_no_request_sends_context_management(monkeypatch, model_id):
    params = {}

    def stream(**kwargs):
        params.update(kwargs)
        return _Stream()

    monkeypatch.setattr("openprogram.models.auth.usage.acquire_pooled", lambda *args: None)
    monkeypatch.setattr(anthropic, "_build_client",
                        lambda *args, **kwargs: (NS(messages=NS(stream=stream)), False))
    model = Model(id=model_id, name="test", provider="anthropic", api="anthropic-messages",
                  base_url="https://api.anthropic.com")

    async def run():
        # Many tool results: the count that used to start clearing them.
        messages = [ToolResultMessage(tool_call_id=f"t{i}", tool_name="read",
                                      content=[TextContent(text="x")], timestamp=0) for i in range(80)]
        return [event async for event in anthropic.stream_simple(
            model, Context(messages=messages), SimpleStreamOptions(api_key="test"))]

    asyncio.run(run())
    assert "context_management" not in params
    assert "context_management" not in (params.get("extra_body") or {})
    assert not hasattr(SimpleStreamOptions(), "cache_edits")
