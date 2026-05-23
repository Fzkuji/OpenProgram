"""rule_tool_aging — collapse out-of-tail tool results into one-line stubs.

Why a tail window: the last few assistant turns are where the model is
actively reasoning about tool output; older results are mostly noise but
still need a trace (which tool ran, rough outcome) so the model can
recall its history. Older tool items therefore flip to state="aged"
with a semantic stub and get locked — once decided, never revisited.

Boundary algorithm matches legacy tool_aging: count assistant items from
the end; the assistant at position ``len-TAIL_TURNS`` (or the first
assistant if there are fewer) is the cutoff. Every tool BEFORE that
cutoff index ages; tools at-or-after stay full.

Protected tools (todo_read / todo_write / web_search) keep their full
rendering even when out of tail — they're short, semantically load-
bearing, and the model relies on them as durable state. We still set
locked=True so downstream rules (microcompact/summarize) skip them too.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..commit.types import ContextItem
from ..tool_aging.policy import (
    MAX_TOOL_RESULT_CHARS,
    PRUNE_PROTECTED_TOOLS,
    TAIL_TURNS,
)
from ..tool_aging.summarize import summarize_tool_call
from ..tool_aging.truncate import middle_truncate
from ._base import RuleContext


def _estimate_tokens(text: str) -> int:
    # 同 context commit 其它地方的粗估口径: 4 char ≈ 1 token. 没必要更精确,
    # 真正的 budget check 用模型自带 tokenizer.
    return max(1, len(text) // 4)


def _extract_tool_meta(
    item: ContextItem,
    ctx: RuleContext,
) -> tuple[str, Any, bool]:
    """Return (tool_name, args, is_error) for a tool item.

    DAG 是真源, 但 fetch_node 可能为 None (summary item / 测试场景),
    这种情况下退化成 "unknown" — 后续 stub 还能用 rendered 当 result.
    """
    name = "unknown"
    args: Any = None
    is_error = False
    if ctx.fetch_node is None:
        return name, args, is_error
    node = ctx.fetch_node(item.source_node_id)
    if not node:
        return name, args, is_error
    # node 结构按 DAG 约定: 优先看 tool / name 字段, args 在 input/args.
    name = node.get("tool") or node.get("name") or name
    args = node.get("input") if "input" in node else node.get("args")
    is_error = bool(node.get("is_error"))
    return name, args, is_error


def rule_tool_aging(items: list[ContextItem], ctx: RuleContext) -> None:
    """Age tool items outside the tail window into stubs; cap oversize tail results.

    只处理 state="full" 且 locked=False 的 tool item; 其它一律跳过。
    """
    # Tail boundary: index of the (TAIL_TURNS-th from end) assistant.
    asst_indices = [i for i, it in enumerate(items) if it.role == "assistant"]
    if not asst_indices:
        # 没 assistant 说明还没真正开 turn, 全留 full.
        tail_cutoff: Optional[int] = None
    elif len(asst_indices) > TAIL_TURNS:
        tail_cutoff = asst_indices[-TAIL_TURNS]
    else:
        tail_cutoff = asst_indices[0]

    for idx, item in enumerate(items):
        if item.role != "tool":
            continue
        if item.locked or item.state != "full":
            continue

        in_tail = tail_cutoff is not None and idx >= tail_cutoff
        name, args, is_error = _extract_tool_meta(item, ctx)
        protected = name in PRUNE_PROTECTED_TOOLS

        if in_tail or protected:
            # 保持 full, 但单条 result 超 hard cap 仍要中间截断 —
            # 一条 50MB 的 tool result 在 tail 里也会爆 window.
            if len(item.rendered) > MAX_TOOL_RESULT_CHARS:
                item.rendered = middle_truncate(item.rendered, MAX_TOOL_RESULT_CHARS)
                item.tokens = _estimate_tokens(item.rendered)
            if protected and not in_tail:
                # 保护类 tool 即使出 tail 也保持 full; lock 住免得下游再动.
                item.locked = True
                item.state_set_at = ctx.commit_id
                item.reason = "protected_tool"
            continue

        # Out of tail and not protected → age it.
        # 没拿到 DAG meta 时用 rendered 自身当 result, summarize_tool_call
        # 的 outcome_blurb 启发式能从纯文本里挤出一行有效信息.
        result_text = item.rendered
        stub = summarize_tool_call(name, args, result_text, is_error)
        item.rendered = stub
        item.tokens = _estimate_tokens(stub)
        item.state = "aged"
        item.locked = True
        item.state_set_at = ctx.commit_id
        item.reason = "tail_window"
