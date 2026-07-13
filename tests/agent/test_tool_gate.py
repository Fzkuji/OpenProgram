"""tool.before 同步问询点（gate）：决策合并、fail-open、端到端真拦截。

端到端复用 test_loop_options 的 fake-provider 模式：注册一个 deny gate，
跑一个完整 AgentSession turn，断言工具 execute 真没被调、模型拿到的是
带理由的 error tool result——这就是迁移步 2 的"能真的拦下"验收。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from openprogram.agent import AgentSession
from openprogram.agent.event_bus import make_event
from openprogram.agent.tool_gate import (
    decide_tool_gate,
    register_tool_gate,
)
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.providers.types import (
    AssistantMessage,
    Model,
    EventDone,
    EventStart,
    TextContent,
    ToolCall,
    ToolResultMessage,
)


@pytest.fixture(autouse=True)
def _clean_gates():
    """每个测试自己注册的 gate 自己拆，互不污染。"""
    from openprogram.agent import tool_gate

    before = list(tool_gate._gates)
    yield
    with tool_gate._lock:
        tool_gate._gates[:] = before


def _ev(tool: str = "bash"):
    return make_event("tool.before", "agent", {"tool": tool, "args": {}})


# decide 语义

def test_no_gates_allows():
    assert decide_tool_gate(_ev()) is None


def test_deny_reason_returned():
    register_tool_gate(lambda ev: "危险命令" if ev.payload["tool"] == "bash" else None)
    assert decide_tool_gate(_ev("bash")) == "危险命令"
    assert decide_tool_gate(_ev("read")) is None


def test_multiple_deny_reasons_merged():
    register_tool_gate(lambda ev: "理由一")
    register_tool_gate(lambda ev: "理由二")
    assert decide_tool_gate(_ev()) == "理由一; 理由二"


def test_raising_gate_fails_open():
    def bad(ev):
        raise RuntimeError("gate bug")

    register_tool_gate(bad)
    assert decide_tool_gate(_ev()) is None


def test_unregister():
    unreg = register_tool_gate(lambda ev: "拦")
    assert decide_tool_gate(_ev()) == "拦"
    unreg()
    assert decide_tool_gate(_ev()) is None


# 端到端：gate 真的拦下工具

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


def _make_stream_fn(replies):
    state = {"calls": 0}

    def stream_fn(model, context, opts):
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


def test_gate_blocks_tool_end_to_end():
    executed = {"n": 0}

    async def _execute(call_id, args, cancel, on_update):
        executed["n"] += 1
        return AgentToolResult(content=[TextContent(text="echoed")])

    tool = AgentTool(
        name="echo",
        description="Echo test tool.",
        parameters={"type": "object", "properties": {}},
        label="echo",
        execute=_execute,
    )
    register_tool_gate(
        lambda ev: "测试拦截" if ev.payload.get("tool") == "echo" else None
    )

    stream_fn, state = _make_stream_fn([
        _assistant([ToolCall(id="c1", name="echo", arguments={})]),
        _assistant([TextContent(text="ok")]),
    ])
    session = AgentSession(
        model=Model(id="fake", name="fake", api="openai-completions",
                    provider="openai",
                    base_url="https://example.invalid/v1"),
        tools=[tool]
    )
    session._agent.stream_fn = stream_fn
    asyncio.run(session.run("go"))

    # 工具真没执行
    assert executed["n"] == 0
    # 模型拿到了带理由的 error tool result
    tool_results = [
        m for m in session._agent.state.messages
        if isinstance(m, ToolResultMessage)
    ]
    assert len(tool_results) == 1
    text = "".join(
        c.text for c in tool_results[0].content if hasattr(c, "text")
    )
    assert "Tool call blocked" in text and "测试拦截" in text
    assert tool_results[0].is_error
