"""ContextCommit 增量生成 — 从上一个 context commit 复制 + 加新节点 + 跑规则.

核心循环: 每个 turn 跑一次 generate_commit(), 产生新 context commit.

不重算: items 是从 parent context commit 直接复制过来的, 已 locked 的 item
任何规则都不会再动它们。新增的 DAG 节点先以 state=full 加入, 然后
规则可能把新增的 / 边界移动到 aging 区的少数 item 标记成 aged/cleared。

规则 pipeline 是固定顺序写死的列表 (从 rules/__init__.py 拿)。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Optional

from .types import ContextItem, ContextCommit, CURRENT_RULES_VERSION


def _estimate_tokens(text: str | None) -> int:
    """粗略 token 估算: 1 token ≈ 4 字符. 替代成真正的 tokenizer 不影响逻辑."""
    if not text:
        return 0
    return max(4, len(text) // 4)


def _gen_commit_id() -> str:
    return "commit_" + uuid.uuid4().hex[:10]


def generate_commit(
    *,
    store,                              # SessionStore
    session_id: str,
    parent_commit,                  # Optional[ContextCommit]
    new_nodes: list[dict[str, Any]],  # 这轮新增的 DAG 节点 (legacy msg dict 格式)
    head_node_id: str,
    budget_total: int,
    budget_summarize_threshold: int,
    fetch_node: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    llm_summarize: Optional[Callable] = None,
) -> ContextCommit:
    """生成新 context commit 并持久化, 返回它.

    new_nodes 是这一轮真正新加进 DAG 的节点 (legacy msg dict 格式: 含
    id/role/content/parent_id/caller/extra/...). 旧 items 从 parent
    context commit 直接复制, 不需要从 DAG 重读。
    """
    from .store import save_commit
    from ..rules import RULE_PIPELINE
    from ..rules._base import RuleContext

    # Step 1: 起点 — 上一 context commit 的 items 拷一份, 全部沿用
    if parent_commit is not None:
        items: list[ContextItem] = [_copy_item(i) for i in parent_commit.items]
    else:
        items = []

    commit_id = _gen_commit_id()

    # Step 2: 追加这轮新增的 DAG 节点为 state="full" 新 item
    for node in new_nodes:
        item = _build_item_from_node(node, commit_id)
        if item is not None:
            items.append(item)

    # Step 3: 跑规则 pipeline (只动 unlocked, 已 locked 跳过)
    ctx = RuleContext(
        commit_id=commit_id,
        session_id=session_id,
        now=time.time(),
        head_node_id=head_node_id,
        budget_total=budget_total,
        budget_summarize_threshold=budget_summarize_threshold,
        fetch_node=fetch_node,
        llm_summarize=llm_summarize,
    )
    for rule in RULE_PIPELINE:
        rule(items, ctx)

    # Step 4: 计算 token 总和, 写 context commit
    total = sum(i.tokens for i in items if i.state != "summarized")
    commit = ContextCommit(
        id=commit_id,
        session_id=session_id,
        parent_id=parent_commit.id if parent_commit else None,
        created_at=time.time(),
        head_node_id=head_node_id,
        rules_version=CURRENT_RULES_VERSION,
        total_tokens=total,
        items=items,
        summary=_describe_changes(items, parent_commit, commit_id),
    )
    save_commit(store, commit)
    return commit


def _build_item_from_node(node: dict, commit_id: str) -> Optional[ContextItem]:
    """legacy msg dict → ContextItem(state=full).

    返回 None = 这个节点不该进 context (system 节点等)。
    """
    role = node.get("role")
    if role not in ("user", "assistant", "tool"):
        return None
    content = node.get("content") or ""
    # tool 节点的 content 可能是 dict, 转成字符串
    if not isinstance(content, str):
        try:
            import json as _json
            content = _json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            content = str(content)
    return ContextItem(
        source_node_id=node.get("id") or "",
        role=role,
        state="full",
        locked=False,
        rendered=content,
        tokens=_estimate_tokens(content),
        state_set_at=commit_id,
        reason="new",
    )


def _copy_item(item: ContextItem) -> ContextItem:
    """浅拷贝 ContextItem. dataclass 默认是 mutable, 不拷贝会被规则改到 parent commit 的 item."""
    return ContextItem(
        source_node_id=item.source_node_id,
        role=item.role,
        state=item.state,
        locked=item.locked,
        rendered=item.rendered,
        tokens=item.tokens,
        state_set_at=item.state_set_at,
        reason=item.reason,
        merged_into=item.merged_into,
        is_anchor=item.is_anchor,
        anchor_for_summary=item.anchor_for_summary,
    )


def _describe_changes(items, parent_snap, commit_id: str) -> str:
    """生成 1 行变化描述, UI timeline 显示用."""
    from collections import Counter
    counts = Counter(i.state for i in items)
    new_count = sum(1 for i in items if i.state_set_at == commit_id and i.reason == "new")
    parts = [f"new={new_count}"]
    for s in ("full", "aged", "cleared", "summarized", "summary"):
        if counts.get(s):
            parts.append(f"{s}={counts[s]}")
    return ", ".join(parts)
