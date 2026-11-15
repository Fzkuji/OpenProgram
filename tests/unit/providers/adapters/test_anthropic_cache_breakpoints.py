"""Where the Anthropic provider puts cache breakpoints in a tool loop.

Every round after the first ends with tool results. The final breakpoint has
to sit on them, so the next round reads the whole history from the cache
instead of only tools and system.
"""
import asyncio
from types import SimpleNamespace as NS

from openprogram.models.providers.adapters.anthropic import anthropic
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


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


def _params(monkeypatch, context, base_url="https://api.anthropic.com", retention=None):
    params = {}

    def stream(**kwargs):
        params.update(kwargs)
        return _Stream()

    monkeypatch.setattr("openprogram.models.auth.usage.acquire_pooled", lambda *args: None)
    monkeypatch.setattr(
        anthropic, "_build_client",
        lambda *args, **kwargs: (NS(messages=NS(stream=stream)), False),
    )
    model = Model(id="claude-sonnet-4-5", name="t", provider="anthropic",
                  api="anthropic-messages", base_url=base_url)
    options = SimpleStreamOptions(api_key="test", cache_retention=retention)

    async def run():
        return [event async for event in anthropic.stream_simple(model, context, options)]

    asyncio.run(run())
    return params


def _marks(params):
    found = []
    for i, block in enumerate(params.get("tools") or []):
        if "cache_control" in block:
            found.append(("tools", i, None, block["cache_control"]))
    for i, block in enumerate(params.get("system") or []):
        if "cache_control" in block:
            found.append(("system", i, None, block["cache_control"]))
    for i, message in enumerate(params["messages"]):
        for block in message["content"]:
            if "cache_control" in block:
                found.append(("messages", i, block["type"], block["cache_control"]))
    return found


_TOOL = Tool(name="read", description="Read a file.",
             parameters={"type": "object", "properties": {"path": {"type": "string"}}})


def _loop(user_content, rounds):
    messages = [UserMessage(content=user_content, timestamp=0)]
    for n in range(rounds):
        messages.append(AssistantMessage(
            role="assistant", api="anthropic-messages", provider="anthropic", model="m",
            stop_reason="toolUse", timestamp=0,
            content=[ToolCall(type="toolCall", id=f"t{n}", name="read", arguments={"path": str(n)})],
        ))
        messages.append(ToolResultMessage(
            role="toolResult", tool_call_id=f"t{n}", tool_name="read", is_error=False, timestamp=0,
            content=[TextContent(type="text", text="contents")],
        ))
    return Context(system_prompt="You are terse.", messages=messages, tools=[_TOOL])


def test_later_round_marks_the_final_tool_result(monkeypatch):
    params = _params(monkeypatch, _loop("do it", rounds=2))
    last = len(params["messages"]) - 1
    assert ("messages", last, "tool_result") in [m[:3] for m in _marks(params)]


def test_block_content_turn_keeps_its_own_mark_and_the_tail(monkeypatch):
    params = _params(monkeypatch, _loop([TextContent(type="text", text="do it")], rounds=2))
    places = [m[:3] for m in _marks(params)]
    last = len(params["messages"]) - 1
    assert ("messages", 0, "text") in places
    assert ("messages", last, "tool_result") in places
    assert len(places) <= 4


def test_first_round_marks_the_person_message(monkeypatch):
    params = _params(monkeypatch, _loop("do it", rounds=0))
    assert ("messages", 0, "text") in [m[:3] for m in _marks(params)]


def test_never_more_than_four_breakpoints(monkeypatch):
    context = _loop([TextContent(type="text", text="do it")], rounds=2)
    first = context.messages[0]
    marked = TextContent(type="text", text="earlier", cache_control={"type": "ephemeral"})
    context.messages[0] = first.model_copy(update={"content": [marked, *first.content]})
    params = _params(monkeypatch, context)
    places = [m[:3] for m in _marks(params)]
    assert len(places) == 4
    assert places[-1] == ("messages", len(params["messages"]) - 1, "tool_result")


def test_long_retention_off_anthropic_keeps_one_lifetime(monkeypatch):
    # Only api.anthropic.com takes a 1h breakpoint; elsewhere every marker is
    # the default lifetime, so no 1h marker follows a 5m one.
    params = _params(monkeypatch, _loop([TextContent(type="text", text="do it")], rounds=1),
                     base_url="https://api.minimax.io/anthropic", retention="long")
    assert {m[3].get("ttl") for m in _marks(params)} == {None}


def test_caller_short_mark_keeps_later_marks_short(monkeypatch):
    # Long retention on api.anthropic.com puts 1h on the provider's own
    # breakpoints. A caller's 5m mark on the person's message comes before the
    # final tool_result breakpoint, and a 1h breakpoint after a 5m one is
    # rejected, so the final one takes the 5m lifetime.
    caller = {"type": "ephemeral"}
    context = _loop([TextContent(type="text", text="rules", cache_control=caller),
                     TextContent(type="text", text="do it")], rounds=1)
    params = _params(monkeypatch, context, retention="long")
    marks = _marks(params)
    lifetimes = [m[3].get("ttl", "5m") for m in marks]
    assert lifetimes[:2] == ["1h", "1h"]
    assert not any(t == "1h" for t in lifetimes[lifetimes.index("5m"):])
    assert marks[-1][:3] == ("messages", len(params["messages"]) - 1, "tool_result")
    assert caller == {"type": "ephemeral"}
