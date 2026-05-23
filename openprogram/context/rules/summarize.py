"""summarize 规则 — 超 budget 时把老 items 合并成一个 summary item.

触发条件: 当前 context commit 总 token > ctx.budget_summarize_threshold.

做的事:
  1. 从最老一端开始, 跳过已 locked / state ∈ {summary, summarized, pinned}
     的 item, 沿连续一段 unlocked 的 items 累积到总量 50%, 划定为
     "被合并范围".
  2. 在该范围内调 anchors.select_anchors 挑 3-5 个锚点, 锚点保留原文
     (state 不动) 只设 is_anchor=True / locked=True / anchor_for_summary=<sid>.
  3. 非锚点 item: state="summarized", locked=True, merged_into=<sid>,
     rendered="" / tokens=0, reason="merged_into:<sid>".
  4. 调 ctx.llm_summarize(被合并范围的所有 item, 含锚点) 拿摘要文本;
     callable 缺失或抛错时退回 _fallback_summary (结构化 stub).
  5. 在被合并范围的开头插一条 role="summary" / state="summary" /
     locked=True 的新 ContextItem, rendered=摘要正文.

为什么 50% 阈值: 砍太少 (10-20%) 节省不了多少 tokens, 砍太多 (80%)
丢上下文严重影响下一个 turn 的连贯性. 一半是 Hermes / OpenClaw 的
经验值。

为什么最少 5 个 item 才值得 summary: 调一次 LLM 几秒 + 几千 tokens
成本, 合并 <5 个 item 划不来, 不如等再多攒点再压。
"""
from __future__ import annotations

import secrets
from typing import Optional

from openprogram.context.commit.types import ContextItem
from openprogram.context.rules._base import RuleContext, total_tokens
from openprogram.context.rules import anchors as _anchors


# 被合并范围至少覆盖 unlocked items 总 token 的 50%, 不到就不触发.
_MERGE_RATIO = 0.5

# 至少这么多 item 才值得 summary (见 module docstring).
_MIN_ITEMS_TO_SUMMARIZE = 5

# 摘要本身预估 ~每 4 字符 1 token (粗略, 跟项目其它地方一致).
_CHARS_PER_TOKEN = 4


def _summary_id() -> str:
    """生成 summary item 的虚拟 source_node_id ('sm_<hex>')."""
    return f"sm_{secrets.token_hex(6)}"


def _is_mergeable(item: ContextItem) -> bool:
    """unlocked 且 state ∈ {full, aged, cleared} 的才参与合并.

    pinned / summary / summarized 都跳过 — pinned 是用户/系统明确要保,
    summary 已经是合并产物不再压, summarized 已被合走没必要再动。
    """
    if item.locked:
        return False
    return item.state in ("full", "aged", "cleared")


def _pick_merge_range(items: list[ContextItem]) -> tuple[int, int]:
    """从最老端走, 累积 mergeable items 的 token, 直到达到总 unlocked
    token 的 _MERGE_RATIO 或撞到不可合并的 item 停下。

    返回 (start, end) 半开区间; end-start < _MIN_ITEMS_TO_SUMMARIZE
    就视为不值得 summary, caller 自己判.
    """
    # 总 unlocked token (分母)
    total_mergeable = sum(i.tokens for i in items if _is_mergeable(i))
    if total_mergeable == 0:
        return (0, 0)
    target = total_mergeable * _MERGE_RATIO

    # 找连续 mergeable 段: 从 i=0 开始 skip 到第一个 mergeable
    n = len(items)
    start = 0
    while start < n and not _is_mergeable(items[start]):
        start += 1
    if start >= n:
        return (0, 0)

    accumulated = 0
    end = start
    while end < n and _is_mergeable(items[end]):
        accumulated += items[end].tokens
        end += 1
        if accumulated >= target:
            break
    return (start, end)


def _serialize_for_llm(items: list[ContextItem]) -> str:
    """把被合并 items 序列化成 'Role: content' 文本, 喂给 LLM."""
    lines: list[str] = []
    for it in items:
        role = (it.role or "user").capitalize()
        text = (it.rendered or "").strip()
        if not text:
            continue
        lines.append(f"{role}: {text}")
    return "\n\n".join(lines)


def _fallback_summary(items: list[ContextItem]) -> str:
    """LLM 不可用时的退路: 拼每条 item 前 200 字符 + 总计.

    比"[N messages elided]"强 — 至少 LLM 还能根据片段提示"我之前看
    过 foo.py 这部分", 必要时可以让 user 补细节.
    """
    n = len(items)
    tok = sum(i.tokens for i in items)
    head = (
        f"[Summary fallback — LLM summariser unavailable. "
        f"{n} message(s) merged, ≈{tok} tokens dropped. "
        f"Heads of each follow; ask user if a specific item matters.]"
    )
    body = []
    for it in items:
        role = (it.role or "?").capitalize()
        snippet = (it.rendered or "").strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:197] + "…"
        body.append(f"  · {role}: {snippet or '(empty)'}")
    return head + "\n" + "\n".join(body)


def rule_summarize(items: list[ContextItem], ctx: RuleContext) -> None:
    """超 budget 时跑 LLM 摘要, 老 items 合并成一个 summary item,
    部分挑出来当锚点保留原文。

    就地改 items: 标记 summarized + 标记锚点 + 在合并范围开头插
    summary item.
    """
    # 1. 触发检查 — 没超阈值不动。
    if total_tokens(items) <= ctx.budget_summarize_threshold:
        return

    # 2. 选合并范围。
    start, end = _pick_merge_range(items)
    if end - start < _MIN_ITEMS_TO_SUMMARIZE:
        return

    sid = _summary_id()
    range_items = items[start:end]

    # 3. 在合并范围内挑锚点 (返回的是相对 items 的全局 indices,
    #    因为 select_anchors 接的是整个列表; 用 set 方便 O(1) 查).
    anchor_indices_global = set(_anchors.select_anchors(items))
    # 锚点必须落在合并范围内 — 范围外的 item 不参与, 也不能锚.
    anchor_indices_global = {
        i for i in anchor_indices_global if start <= i < end
    }

    # 4. 跑 LLM (或 fallback). 注意喂给 LLM 的是被合并范围的全部 item
    #    (含锚点) — 锚点也参与摘要生成, 这样摘要才完整; 只是锚点同时
    #    也保留原文给 LLM 后续 turn 看。
    llm_fn = ctx.llm_summarize
    summary_text: str
    if llm_fn is None:
        summary_text = _fallback_summary(range_items)
    else:
        try:
            summary_text = llm_fn(range_items)
        except Exception as e:  # noqa: BLE001
            summary_text = (
                _fallback_summary(range_items)
                + f"\n[LLM summariser failed: {type(e).__name__}: {e}]"
            )

    # 5. 标记非锚点 → summarized; 标记锚点 → is_anchor + locked.
    for idx in range(start, end):
        it = items[idx]
        if idx in anchor_indices_global:
            it.is_anchor = True
            it.anchor_for_summary = sid
            it.locked = True
            it.state_set_at = ctx.commit_id
            it.reason = f"anchor_for:{sid}"
            # state 不动 — 锚点保留原本的 full / aged / cleared.
        else:
            it.state = "summarized"
            it.locked = True
            it.merged_into = sid
            it.rendered = ""
            it.tokens = 0
            it.state_set_at = ctx.commit_id
            it.reason = f"merged_into:{sid}"

    # 6. 造 summary item, 插到合并范围开头位置。
    #    state="summary" 表示这条本身就是合成的, locked=True 后续规则不动.
    summary_item = ContextItem(
        source_node_id=sid,
        role="summary",
        state="summary",
        locked=True,
        rendered=summary_text,
        tokens=max(1, len(summary_text) // _CHARS_PER_TOKEN),
        state_set_at=ctx.commit_id,
        reason=f"summarize_merged:{end - start}",
    )
    # 插入: 把被合并段开头的 item 替换前置入 summary. 注意被合并的
    # summarized item 我们保留在 items 里 (state=summarized 不渲染),
    # 这样 DAG ↔ context commit 对应关系不丢, 只是 render 时跳过。
    items.insert(start, summary_item)
