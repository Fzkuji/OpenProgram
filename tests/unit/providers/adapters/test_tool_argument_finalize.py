"""Stream finalize tolerates tool-call arguments that parse to a non-object.

A relay whose model emits tool arguments as a top-level JSON scalar or array
("42", "[1,2]") reaches the finalize with a truthy non-dict parse result.
The ToolCall schema requires an object, so constructing it raised a
pydantic ValidationError and killed the turn; the Responses path already
falls back to empty arguments (shared/openai_responses.py). These tests pin
the same fallback on the Anthropic and Completions paths.
"""
from types import SimpleNamespace as NS

import asyncio

from openprogram.models.providers.adapters.anthropic import anthropic
from openprogram.models.providers.adapters.openai_completions import openai_completions
from openprogram.models.providers.types import (
    Context,
    Model,
    SimpleStreamOptions,
    Tool,
    UserMessage,
)

_TOOL = Tool(
    name="bash",
    description="Run a shell command",
    parameters={"type": "object", "properties": {"command": {"type": "string"}}},
)


def _context() -> Context:
    return Context(system_prompt="", messages=[UserMessage(content="run it", timestamp=0)],
                   tools=[_TOOL])


def _collect(gen) -> list:
    async def run():
        return [event async for event in gen]

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# anthropic-messages
# ---------------------------------------------------------------------------

class _AnthropicStream:
    def __init__(self, events):
        self._events = list(events)
        self.response = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def get_final_message(self):
        return NS(usage=NS(input_tokens=3, output_tokens=1), stop_reason="tool_use")


class _AnthropicClient:
    def __init__(self, events):
        self._events = events
        self.messages = NS(stream=lambda **params: _AnthropicStream(self._events))


def test_anthropic_non_object_tool_arguments_finalize_to_empty(monkeypatch):
    model = Model(id="claude-x", name="x", api="anthropic-messages", provider="anthropic",
                  base_url="https://example.invalid")
    events = [
        NS(type="message_start", message=NS(usage=NS(input_tokens=3, output_tokens=1))),
        NS(type="content_block_start", index=0,
           content_block=NS(type="tool_use", id="toolu_1", name="bash")),
        NS(type="content_block_delta", index=0,
           delta=NS(type="input_json_delta", partial_json="42")),
        NS(type="content_block_stop", index=0),
        NS(type="message_delta", delta=NS(stop_reason="tool_use"), usage=None),
        NS(type="message_stop"),
    ]
    monkeypatch.setattr(anthropic, "_build_client",
                        lambda *args, **kwargs: (_AnthropicClient(events), False))

    collected = _collect(anthropic.stream_simple(
        model, _context(), SimpleStreamOptions(api_key="test-key")))

    tool_ends = [e for e in collected if e.type == "toolcall_end"]
    assert len(tool_ends) == 1
    assert tool_ends[0].tool_call.name == "bash"
    assert tool_ends[0].tool_call.arguments == {}
    assert collected[-1].type == "done"


# ---------------------------------------------------------------------------
# openai-completions
# ---------------------------------------------------------------------------

class _CompletionsStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.response = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _CompletionsClient:
    def __init__(self, chunks):
        self._chunks = chunks
        self.chat = NS(completions=self)

    async def create(self, **params):
        return _CompletionsStream(self._chunks)


def test_completions_non_object_tool_arguments_finalize_to_empty(monkeypatch):
    model = Model(id="gpt-x", name="x", api="openai-completions", provider="openai",
                  base_url="https://example.invalid/v1")
    tc_delta = NS(index=0, id="call_1", type="function",
                  function=NS(name="bash", arguments="42"))
    chunks = [
        NS(usage=None, service_tier=None, choices=[
            NS(delta=NS(content=None, reasoning_content=None, tool_calls=[tc_delta]),
               finish_reason=None),
        ]),
        NS(usage=None, service_tier=None, choices=[
            NS(delta=NS(content=None, reasoning_content=None, tool_calls=None),
               finish_reason="tool_calls"),
        ]),
    ]
    fake_sdk = NS(AsyncOpenAI=lambda **kwargs: _CompletionsClient(chunks),
                  APIError=type("APIError", (Exception,), {}))
    monkeypatch.setattr(openai_completions, "_sdk", lambda: fake_sdk)
    monkeypatch.setattr(
        "openprogram.models.providers.utils.http_transport.http_client.get_shared_async_client",
        lambda *args, **kwargs: None,
    )

    collected = _collect(openai_completions.stream_simple(
        model, _context(), SimpleStreamOptions(api_key="test-key")))

    tool_ends = [e for e in collected if e.type == "toolcall_end"]
    assert len(tool_ends) == 1
    assert tool_ends[0].tool_call.name == "bash"
    assert tool_ends[0].tool_call.arguments == {}
    assert collected[-1].type == "done"
