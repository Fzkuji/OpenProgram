"""ensure_latest_snapshot — engine 接入 snapshot 系统的统一入口.

每个 turn 的 prepare 阶段调一次:

    snap = ensure_latest_snapshot(session_id, history, head_id, budget, ...)
    messages = render_snapshot(snap)

逻辑:
  1. 加载本 session 最新 snapshot.
  2. 如果它的 head_node_id 跟当前 head 一致 → 直接返回 (已是最新).
  3. 否则计算 delta: history 里跟 snap 不重合的新节点, 调
     generate_snapshot 产新 snap.
  4. 老 session 第一次跑没 snapshot — 把全部 history 当 new_nodes 喂
     给 generator 一次性 cold-start, 产生 snap_0.

冷启动只发生一次, 之后都是 O(delta).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .types import Snapshot
from .store import load_latest_snapshot
from .generator import generate_snapshot


def ensure_latest_snapshot(
    *,
    db_path: str,
    session_id: str,
    history: list[dict[str, Any]],
    head_node_id: str,
    budget_total: int,
    budget_summarize_threshold: int,
    fetch_node: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    llm_summarize: Optional[Callable] = None,
) -> Snapshot:
    """Return the snapshot reflecting current head; generate if stale.

    ``history`` 是 dispatcher 传进来的 legacy msg-dict 列表 (conv chain
    + tool sub-calls 已经组合好). 我们用它当 cold-start 或 delta 计算的
    数据源 — 不再从 DAG 直接拉。
    """
    latest = load_latest_snapshot(db_path, session_id)

    # Case 1: no snapshot yet — cold start, use the full history as
    # initial input. This only happens once per session.
    if latest is None:
        return generate_snapshot(
            db_path=db_path,
            session_id=session_id,
            parent_snapshot=None,
            new_nodes=history,
            head_node_id=head_node_id,
            budget_total=budget_total,
            budget_summarize_threshold=budget_summarize_threshold,
            fetch_node=fetch_node,
            llm_summarize=llm_summarize,
        )

    # Case 2: head matches — nothing changed since last snap, return as-is.
    if latest.head_node_id == head_node_id:
        return latest

    # Case 3: delta — find nodes that exist in history but not yet in
    # latest snapshot's items. We key by source_node_id, which equals
    # the DAG node id for non-summary items.
    known_ids = {
        i.source_node_id for i in latest.items
        if not i.source_node_id.startswith("sm_")
    }
    new_nodes = [
        n for n in history
        if n.get("id") and n.get("id") not in known_ids
    ]

    if not new_nodes:
        # 罕见: head 不同但 history 跟 snap 完全一致. 可能是 head
        # 切到 retry 分支, 这种情况现在不处理 (后续 phase 加 fork),
        # 暂时直接返回老 snap.
        return latest

    return generate_snapshot(
        db_path=db_path,
        session_id=session_id,
        parent_snapshot=latest,
        new_nodes=new_nodes,
        head_node_id=head_node_id,
        budget_total=budget_total,
        budget_summarize_threshold=budget_summarize_threshold,
        fetch_node=fetch_node,
        llm_summarize=llm_summarize,
    )
