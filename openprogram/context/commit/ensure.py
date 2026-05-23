"""ensure_latest_commit — engine 接入 context commit 系统的统一入口.

每个 turn 的 prepare 阶段调一次. 接受 SessionStore (而非 db_path)
作为后端 — git-as-truth 之后, 所有 context commit IO 走 SessionStore 的
GitSession.

逻辑:
  1. 加载本 session 最新 context commit.
  2. 如果它的 head_node_id 跟当前 head 一致 → 直接返回.
  3. 否则计算 delta: history 里跟 commit 不重合的新节点, 调
     generate_commit 产新 commit.
  4. 老 session 第一次跑没 context commit — 把全部 history 当 new_nodes 喂
     给 generator 一次性 cold-start.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .types import ContextCommit
from .store import load_latest_commit
from .generator import generate_commit


def ensure_latest_commit(
    *,
    store,                              # SessionStore
    session_id: str,
    history: list[dict[str, Any]],
    head_node_id: str,
    budget_total: int,
    budget_summarize_threshold: int,
    fetch_node: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    llm_summarize: Optional[Callable] = None,
) -> ContextCommit:
    latest = load_latest_commit(store, session_id)

    if latest is None:
        return generate_commit(
            store=store,
            session_id=session_id,
            parent_commit=None,
            new_nodes=history,
            head_node_id=head_node_id,
            budget_total=budget_total,
            budget_summarize_threshold=budget_summarize_threshold,
            fetch_node=fetch_node,
            llm_summarize=llm_summarize,
        )

    if latest.head_node_id == head_node_id:
        return latest

    known_ids = {
        i.source_node_id for i in latest.items
        if not i.source_node_id.startswith("sm_")
    }
    new_nodes = [
        n for n in history
        if n.get("id") and n.get("id") not in known_ids
    ]

    if not new_nodes:
        return latest

    return generate_commit(
        store=store,
        session_id=session_id,
        parent_commit=latest,
        new_nodes=new_nodes,
        head_node_id=head_node_id,
        budget_total=budget_total,
        budget_summarize_threshold=budget_summarize_threshold,
        fetch_node=fetch_node,
        llm_summarize=llm_summarize,
    )
