"""A turn whose tool rounds keep coming back the same ends with a notice.

Eight rounds in a row of the same calls, arguments and results stop the
turn; a changed result, a wait, or a word from the user starts the count
again.
"""
from __future__ import annotations

import asyncio
import time

from openprogram.models.providers.types import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    EventDone,
    EventStart,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.tool_loop_guard import TOOL_LOOP_REPEATS, ToolLoopGuard
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig


def _asked(*calls: tuple[str, dict], round_no: int = 0) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(id=f"r{round_no}-{i}", name=name, arguments=args)
                 for i, (name, args) in enumerate(calls)],
        api="completion", provider="fake", model="stub",
        stop_reason="toolUse", timestamp=1,
    )


def _results(message: AssistantMessage, text: str) -> list[ToolResultMessage]:
    return [
        ToolResultMessage(tool_call_id=call.id, tool_name=call.name,
                          content=[TextContent(text=text)], timestamp=1)
        for call in message.content
    ]


def _rounds(guard: ToolLoopGuard, count: int, *, calls=(("read", {"path": "a.py"}),),
            text=lambda n: "same"):
    tripped = None
    for n in range(count):
        asked = _asked(*calls, round_no=n)
        tripped = guard.observe(asked, _results(asked, text(n)))
    return tripped


def test_the_eighth_identical_round_trips_and_not_before():
    guard = ToolLoopGuard()
    assert _rounds(guard, TOOL_LOOP_REPEATS - 1) is None
    asked = _asked(("read", {"path": "a.py"}), round_no=99)
    stuck = guard.observe(asked, _results(asked, "same"))
    assert stuck is not None and stuck.rounds == TOOL_LOOP_REPEATS
    assert 'read {"path": "a.py"}' in stuck.notice()


def test_a_result_that_moves_is_progress():
    guard = ToolLoopGuard()
    assert _rounds(guard, 20, text=lambda n: f"status {n // 3}") is None


def test_argument_order_and_elapsed_time_do_not_tell_rounds_apart():
    guard = ToolLoopGuard()
    for n in range(TOOL_LOOP_REPEATS):
        args = {"a": 1, "b": 2} if n % 2 else {"b": 2, "a": 1}
        asked = _asked(("run", args), round_no=n)
        stuck = guard.observe(asked, _results(asked, f"(elapsed {n}.{n}s)\nok"))
    assert stuck is not None


def test_waits_are_never_a_loop():
    guard = ToolLoopGuard()
    assert _rounds(guard, 20, calls=(("clock__sleep", {"duration_ms": 60000}),)) is None
    assert _rounds(guard, 20, calls=(("waitForCi", {"run": 7}),)) is None
    assert _rounds(guard, 20, calls=(("anything", {"seconds": 30}),)) is None


def test_a_wait_beside_another_call_is_judged_as_usual():
    guard = ToolLoopGuard()
    calls = (("sleep", {"seconds": 5}), ("read", {"path": "a.py"}))
    assert _rounds(guard, TOOL_LOOP_REPEATS, calls=calls) is not None


def test_reset_starts_the_count_again():
    guard = ToolLoopGuard()
    _rounds(guard, TOOL_LOOP_REPEATS - 1)
    guard.reset()
    assert _rounds(guard, TOOL_LOOP_REPEATS - 1) is None


def test_the_agent_loop_ends_the_turn_with_the_notice():
    executed: list[str] = []

    async def execute(call_id, _args, _cancel, _on_update):
        executed.append(call_id)
        return AgentToolResult(content=[TextContent(text="the same file")])

    tool = AgentTool(name="read", label="read", description="read",
                     parameters={"type": "object"}, execute=execute)
    requests = []

    def stream_fn(_model, _context, _options):
        requests.append(1)
        asked = _asked(("read", {"path": "a.py"}), round_no=len(requests))

        async def events():
            yield EventStart(partial=asked)
            yield EventDone(reason="toolUse", message=asked)

        return events()

    async def run():
        stream = agent_loop(
            [UserMessage(content="inspect", timestamp=1)],
            AgentContext(tools=[tool], memory_prefetch=""),
            AgentLoopConfig(
                model=Model(id="stub", name="stub", api="completion", provider="fake",
                            base_url="https://example.invalid"),
                convert_to_llm=lambda messages: messages,
            ),
            stream_fn=stream_fn,
        )
        async for _ in stream:
            pass
        return await stream.result()

    messages = asyncio.run(asyncio.wait_for(run(), 10))
    assert len(requests) == TOOL_LOOP_REPEATS
    assert len(executed) == TOOL_LOOP_REPEATS
    last = messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "error"
    assert "same tool call 8 times" in last.error_message
    assert last.error_retryable is False
