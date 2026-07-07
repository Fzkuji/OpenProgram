"""exec → breakdown：走 _call_via_providers 路径时，现算的输入分类分解
挂上 runtime._pending_breakdown，工具名单进 llm 节点 metadata.tools_available。

模板复用 test_runtime_exec_dag.py::test_exec_stream_fn_injection（stream_fn 注入
provider 路径，无网络）。fake 的 EventDone 不带 usage，所以 last_usage["breakdown"]
的挂载不测（那依赖 final.usage）；这里测 session.run 之前就算好的 _pending_breakdown
与随节点落盘的 tools_available。
"""
from __future__ import annotations

import time as _time
from pathlib import Path

import pytest

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.store import GraphStoreShim, SessionStore, _store as _store_var


@pytest.fixture
def store(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", agent_id="main")
    s = GraphStoreShim(store, "s1")
    token = _store_var.set(s)
    try:
        yield s
    finally:
        _store_var.reset(token)


def _fake_stream_factory():
    from openprogram.providers.types import (
        AssistantMessage, TextContent,
        EventStart, EventTextStart, EventTextEnd, EventDone,
    )

    async def fake_stream(model, context, options=None):
        def _msg(text):
            return AssistantMessage(
                content=[TextContent(text=text)],
                api="completion", provider="callable", model="fake",
                stop_reason="stop", timestamp=int(_time.time() * 1000),
            )
        yield EventStart(partial=_msg(""))
        yield EventTextStart(content_index=0, partial=_msg(""))
        yield EventTextEnd(content_index=0, content="done", partial=_msg("done"))
        yield EventDone(reason="stop", message=_msg("done"))

    return fake_stream


def _runtime():
    from openprogram.providers.types import Model
    rt = Runtime(model="default")
    rt.api_model = Model(
        id="fake", name="fake", api="completion", provider="callable", base_url="",
    )
    return rt


def test_pending_breakdown_computed_on_provider_path(store):
    """走 _call_via_providers 时，_pending_breakdown 被现算填上。"""
    rt = _runtime()
    fake = _fake_stream_factory()

    @agentic_function
    def ask(q, runtime=None):
        # toolset="none" → 无工具，breakdown 仍应算出（tools_schema==0）
        return runtime.exec(f"q: {q}", stream_fn=fake, toolset="none")

    ask("hello", runtime=rt)

    bd = getattr(rt, "_pending_breakdown", None)
    assert bd is not None
    assert bd["source"] == "local_tiktoken"
    assert bd["tools_schema"] == 0          # toolset none
    assert bd["input_used"] > 0             # system+messages 有值


def test_tools_available_persisted_to_node(store):
    """带默认工具的调用，工具名单进 llm 节点 metadata.tools_available。"""
    rt = _runtime()
    fake = _fake_stream_factory()

    @agentic_function
    def ask(q, runtime=None):
        return runtime.exec(f"q: {q}", stream_fn=fake)  # 默认 toolset → 有工具

    ask("hello", runtime=rt)

    g = store.load()
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(llm_nodes) == 1
    meta = llm_nodes[0].metadata or {}
    tools = meta.get("tools_available")
    assert tools, "tools_available should be recorded on the llm node"
    assert isinstance(tools, list) and len(tools) > 0
