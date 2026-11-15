"""A stored tool result takes at most its share of a small model window."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from openprogram.models.providers.types import AgentTool, AgentToolResult, AssistantMessage, TextContent, ToolCall
from openprogram.models.providers.utils.event_stream import EventStream
from openprogram.runtime.agent.loop import _execute_tool_calls
from openprogram.state.store.session.weight import weight
from openprogram.state.store.session import spill

LINES = "\n".join(f"row {i:05d}: " + "alpha bravo charlie " * 3 for i in range(1_000))


@pytest.mark.parametrize(("window", "cap"), [
    (None, 10_000),        # unknown window: the default weight
    (500_000, 10_000),     # a large window never raises it
    (65_536, 10_000),      # 30% of this window is more than the default
    (32_768, 9_830),       # 30% of the window, in weight
    (8_000, 2_400),        # the share holds in a small window too
    (4_096, 1_228),        # a hand-added local model's default window
])
def test_the_cap_is_the_windows_share(window, cap) -> None:
    token = spill.set_window(window)
    try:
        assert spill.result_cap() == cap
    finally:
        spill.reset_window(token)
    assert spill.result_cap() == spill.DEFAULT_TOOL_OUTPUT_WEIGHT


def _call(tool: AgentTool, *, context_window: int | None) -> str:
    message = AssistantMessage(
        content=[ToolCall(id="call_big", name=tool.name, arguments={})],
        api="openai-completions", provider="openai", model="fake",
        stop_reason="toolUse", timestamp=int(time.time() * 1000),
    )
    out = asyncio.run(_execute_tool_calls([tool], message, None, EventStream(), None, {},
                                          context_window=context_window))
    [result] = out["tool_results"]
    [text] = [block.text for block in result.content if isinstance(block, TextContent)]
    return text


def _big_tool(rooms: list[int]) -> AgentTool:
    async def execute(call_id, args, cancel, on_update):
        rooms.append(spill.result_cap())
        return AgentToolResult(content=[TextContent(text=LINES)], details={})

    return AgentTool(name="big", description="big", parameters={"type": "object", "properties": {}},
                     label="big", execute=execute)


def test_a_small_window_bounds_the_stored_result_and_keeps_the_whole_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("openprogram.programs._host.runtime._tool_results_dir", lambda: tmp_path)
    rooms: list[int] = []
    text = _call(_big_tool(rooms), context_window=32_768)
    assert rooms == [9_830]
    assert weight(text) <= 9_830 < weight(LINES)
    [saved] = list(tmp_path.iterdir())
    assert saved.read_text() == LINES and str(saved) in text
    assert spill.result_cap() == spill.DEFAULT_TOOL_OUTPUT_WEIGHT  # the window does not outlive the calls


def test_a_large_window_keeps_the_default_weight(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("openprogram.programs._host.runtime._tool_results_dir", lambda: tmp_path)
    rooms: list[int] = []
    text = _call(_big_tool(rooms), context_window=500_000)
    cap = spill.DEFAULT_TOOL_OUTPUT_WEIGHT
    assert rooms == [cap]
    assert cap - 250 < weight(text) <= cap < weight(LINES)
