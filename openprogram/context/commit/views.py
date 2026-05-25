"""ContextCommit → provider Message 渲染.

把一个 ContextCommit 的 items 列表翻译成 provider 能消费的 Message 对象列表
(UserMessage / AssistantMessage / ToolResultMessage).

纯函数, 不调 DB / LLM. 输入是 ContextCommit, 输出是 list[Message].

约束:
  * state="summarized" 的 item 跳过 — 已合进 summary item, 不再渲染
  * state="summary" 的 item 渲染成 AssistantMessage(prefix="[Summary]")
  * tool_call 跟它的 tool_result 配对: assistant 节点的 ToolCall 跟同
    context commit 后续的 ToolResultMessage 用 source_node_id 关联
"""
from __future__ import annotations

import json
import time
from typing import Any

from .types import ContextItem, ContextCommit


def render_commit(commit: ContextCommit) -> list[Any]:
    """Return provider Message[] for the context commit, in render order.

    Lazy import providers.types 避免循环 — context engine 可能在
    provider 模块还没注册时被引入。
    """
    from openprogram.providers.types import (
        AssistantMessage,
        TextContent,
        UserMessage,
    )

    out: list[Any] = []
    now_ms = int(time.time() * 1000)

    # 索引一下 tool item, 便于查 caller (但这里我们假设 ContextItem 都
    # 是平的, 没显式 caller 链 — 简化版本: 按列表顺序渲染, assistant
    # 之后紧跟它的 tool result 是约定俗成的写入顺序)
    for item in commit.items:
        if item.state == "summarized":
            continue   # 已合进 summary, 跳过
        if not item.rendered and item.state != "summary":
            continue   # 空内容直接跳

        ts = now_ms

        if item.role == "user":
            out.append(UserMessage(
                content=[TextContent(text=item.rendered)],
                timestamp=ts,
            ))
        elif item.role == "summary":
            # summary item 走 assistant message 通道, 前缀 [Summary]
            # 让 LLM 识别这是合成内容. AssistantMessage 不接受外部
            # tool call, 只塞文本.
            out.append(_make_assistant(
                content=f"[Summary]\n{item.rendered}",
                timestamp=ts,
            ))
        elif item.role == "assistant":
            out.append(_make_assistant(
                content=item.rendered,
                timestamp=ts,
            ))
        elif item.role == "tool":
            # tool item 不能直接走 ToolResultMessage —— ContextItem
            # 不携带 tool_call_id 跟 tool_calls 配对信息, 而 codex /
            # OpenAI Responses 协议要求 function_call_output 必须有
            # 对应的 function_call (否则 HTTP 400 "No tool call found
            # for function call output"). _make_assistant() 不 emit
            # ToolCall block, 所以配对永远凑不齐. 把 tool 节点降级成
            # 一条 user 文本消息: LLM 能看到工具结果, 但不进入
            # function_call 协议路径.
            out.append(UserMessage(
                content=[TextContent(text=f"[tool_result]\n{item.rendered}")],
                timestamp=ts,
            ))
    return out


def _make_assistant(content: str, timestamp: int) -> Any:
    """构造 AssistantMessage. provider.types 要求 api/provider/model
    字段, 这里用占位符 — engine 不会用 view 输出的这些字段做决策,
    实际 provider 调用从 agent profile 拿 model.
    """
    from openprogram.providers.types import AssistantMessage, TextContent
    return AssistantMessage(
        content=[TextContent(text=content)],
        api="completion",
        provider="openai",
        model="gpt-5",
        timestamp=timestamp,
    )


def commit_token_total(commit: ContextCommit) -> int:
    """Sum 实际渲染部分的 tokens (summarized 不算)."""
    return sum(i.tokens for i in commit.items if i.state != "summarized")


def commit_state_counts(commit: ContextCommit) -> dict[str, int]:
    """state → count, UI timeline / debugging 用."""
    from collections import Counter
    return dict(Counter(i.state for i in commit.items))
