"""单次 LLM 调用的输入 token 分类分解（对齐 Claude Code /context）。

存储铁律（论文仓库 spec §3）：本模块只算，不存。结果是原料的确定
函数；调用方拿走塞进自己需要的地方，或事后从 history 重算（compute_breakdown_from_node）。
"""
from __future__ import annotations

from typing import Any

from openprogram.context.budget import (
    default_allocator,
    estimate_tools_breakdown,
)
from openprogram.context.tokens import estimate_message_tokens


def _catalog_tokens(catalog: list) -> int:
    """deferred 目录块的真实成本 = 渲染出来的那段文字。

    目录只发裸工具名（``deferred_catalog_text`` 用 name 拼行，描述不发），
    所以按 name+description 计价会高估一个数量级。直接对渲染结果计数，
    口径永远跟着渲染函数走。"""
    if not catalog:
        return 0
    from openprogram.programs import deferred_catalog_text
    return estimate_message_tokens(
        {"role": "system", "content": deferred_catalog_text(list(catalog))}
    )


def compute_call_breakdown(
    *,
    system_prompt: str,
    history: list[dict],
    tools: list[Any] | None,
    context_window: int,
) -> dict:
    """把一次调用的 input 拆成分类 + per-tool。见论文仓库 spec §5。"""
    tools = tools or []
    # loaded vs deferred：只有 loaded（带 schema）计入 tools_schema
    try:
        from openprogram.programs import split_tools_for_dispatch
        provider_tools, catalog = split_tools_for_dispatch(tools)
    except Exception:
        provider_tools, catalog = tools, []

    alloc = default_allocator.allocate(
        context_window=context_window,
        system_prompt=system_prompt,
        history=history,
        tools=provider_tools,
    )
    per_tool = estimate_tools_breakdown(tools)
    return {
        "messages": alloc.history,
        "system_prompt": alloc.system_prompt,
        "skills": 0,   # MVP：已并入 system_prompt 文本
        "memory": 0,   # MVP：同上
        "tools_schema": alloc.tools_schema,
        "tools_deferred_catalog": _catalog_tokens(catalog),
        "mcp_tools": 0,  # MVP：MCP 工具走同一 tools 列表，已计入 per-tool
        "input_used": alloc.input_used,
        "input_used_pct": round(alloc.input_used_pct, 4),
        "tools": per_tool,
        "source": "local_tiktoken",
    }


def _default_tool_resolver(names: list[str]) -> list:
    """工具名 → 工具对象（带 schema）。默认走 functions.agent_tools。"""
    if not names:
        return []
    try:
        from openprogram.programs import agent_tools
        return agent_tools(names=names)
    except Exception:
        return []


def compute_breakdown_from_node(
    node: dict,
    *,
    history: list[dict],
    system_prompt: str,
    context_window: int,
    tool_resolver=None,
) -> dict:
    """事后从一个 history 节点重算 breakdown（重算路 / 论文时间序列）。
    工具名从 node.metadata.tools_available 还原，schema 从 registry 现取。
    tool_resolver 可注入（测试用）；默认 functions.agent_tools。"""
    meta = node.get("metadata") or {}
    names = meta.get("tools_available") or []
    resolver = tool_resolver or _default_tool_resolver
    tools = resolver(list(names))
    return compute_call_breakdown(
        system_prompt=system_prompt,
        history=history,
        tools=tools,
        context_window=context_window,
    )


def compute_breakdown_for_branch(
    branch: list[dict],
    *,
    system_prompt: str,
    context_window: int,
    tool_resolver=None,
) -> dict:
    """给一整条会话分支（消息列表），算这条分支下一次调用的 breakdown。

    工具集取分支里最近一个带 metadata.tools_available 的节点（最近一次
    LLM 调用挂了哪些工具）；整条分支当 history。供 web /context 端点直接
    消费——「随时看当前会话的 context 构成」。见论文仓库 spec §5。"""
    latest_tools: list[str] = []
    for msg in reversed(branch or []):
        meta = msg.get("metadata") or {}
        ta = meta.get("tools_available")
        if ta:
            latest_tools = list(ta)
            break
    resolver = tool_resolver or _default_tool_resolver
    tools = resolver(latest_tools)
    return compute_call_breakdown(
        system_prompt=system_prompt,
        history=branch or [],
        tools=tools,
        context_window=context_window,
    )
