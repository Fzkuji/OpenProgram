"""exec-level loop options (tool_choice / parallel_tool_calls /
max_iterations) reach the agent loop and the provider call.

These three were accepted-but-inert from the AgentSession unification
(dd4c87c6) until the 2026-06 re-wiring; the tests pin the whole chain —
exec kwargs → _current_loop_opts → AgentSession → AgentLoopConfig →
stream options / loop cap — so it cannot silently break again.
"""

from __future__ import annotations

import asyncio
import time

from openprogram.agent import AgentSession
from openprogram.agent.types import AgentToolResult
from openprogram.providers import get_model
from openprogram.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    TextContent,
    ToolCall,
)
from openprogram.agentic_programming.runtime import (
    Runtime,
    _current_loop_opts,
)


def _assistant(content) -> AssistantMessage:
    has_calls = any(isinstance(c, ToolCall) for c in content)
    return AssistantMessage(
        content=content,
        api="openai-completions",
        provider="openai",
        model="fake",
        stop_reason="toolUse" if has_calls else "stop",
        timestamp=int(time.time() * 1000),
    )


def _tool_call_msg(i: int = 0) -> AssistantMessage:
    return _assistant([ToolCall(id=f"call_{i}", name="echo", arguments={})])


def _text_msg() -> AssistantMessage:
    return _assistant([TextContent(text="done")])


def _make_stream_fn(replies: list[AssistantMessage]):
    """Fake provider: returns replies in order (last one repeats),
    records how many model calls happened and the opts of each."""
    state = {"calls": 0, "opts": []}

    def stream_fn(model, context, opts):
        state["opts"].append(opts)
        idx = min(state["calls"], len(replies) - 1)
        state["calls"] += 1
        msg = replies[idx]

        async def gen():
            yield EventStart(partial=msg)
            yield EventDone(
                reason="toolUse" if msg.stop_reason == "toolUse" else "stop",
                message=msg,
            )

        return gen()

    return stream_fn, state


async def _echo_execute(call_id, args, cancel, on_update):
    return AgentToolResult(content=[TextContent(text="echoed")])


def _echo_tool():
    from openprogram.agent.types import AgentTool

    return AgentTool(
        name="echo",
        description="Echo test tool.",
        parameters={"type": "object", "properties": {}},
        label="echo",
        execute=_echo_execute,
    )


def _session(stream_fn, **kwargs) -> AgentSession:
    session = AgentSession(
        model=get_model("openai", "gpt-4o-mini"),
        tools=[_echo_tool()],
        **kwargs,
    )
    session._agent.stream_fn = stream_fn
    return session


def test_max_iterations_caps_the_loop():
    # The model always asks for one more tool call; the cap must stop it.
    stream_fn, state = _make_stream_fn([_tool_call_msg()])
    session = _session(stream_fn, max_iterations=2)
    asyncio.run(session.run("go"))
    assert state["calls"] == 2


def test_default_cap_does_not_kick_in_early():
    # Three tool rounds then a text answer — with no caller cap the
    # loop must run all four model calls.
    stream_fn, state = _make_stream_fn(
        [_tool_call_msg(0), _tool_call_msg(1), _tool_call_msg(2), _text_msg()]
    )
    session = _session(stream_fn)
    asyncio.run(session.run("go"))
    assert state["calls"] == 4


def test_tool_choice_and_parallel_reach_stream_opts():
    stream_fn, state = _make_stream_fn([_text_msg()])
    session = _session(
        stream_fn, tool_choice="required", parallel_tool_calls=False
    )
    asyncio.run(session.run("go"))
    opts = state["opts"][0]
    assert opts.tool_choice == "required"
    assert opts.parallel_tool_calls is False


def test_defaults_leave_stream_opts_unset():
    stream_fn, state = _make_stream_fn([_text_msg()])
    session = _session(stream_fn)
    asyncio.run(session.run("go"))
    opts = state["opts"][0]
    assert opts.tool_choice is None
    assert opts.parallel_tool_calls is None


# exec → _current_loop_opts normalisation


class _ProbeRuntime(Runtime):
    """Capture what exec() published to _current_loop_opts at call time."""

    def __init__(self, captured: dict):
        super().__init__(call=lambda *a, **kw: "ok", model="dummy")
        self._captured = captured

    def _call(self, content, model="default", response_format=None):
        self._captured["opts"] = _current_loop_opts.get(None)
        return "ok"


def test_exec_publishes_only_non_default_loop_opts():
    captured: dict = {}
    rt = _ProbeRuntime(captured)
    rt.exec(
        [{"type": "text", "text": "hi"}],
        tool_choice="required",
        parallel_tool_calls=False,
        max_iterations=5,
    )
    assert captured["opts"] == {
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "max_iterations": 5,
    }


def test_exec_defaults_publish_only_the_20_round_cap():
    # "auto" / True are provider defaults and must not travel; the
    # documented default cap of 20 rounds does.
    captured: dict = {}
    rt = _ProbeRuntime(captured)
    rt.exec([{"type": "text", "text": "hi"}])
    assert captured["opts"] == {"max_iterations": 20}
