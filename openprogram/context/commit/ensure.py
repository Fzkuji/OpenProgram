"""ensure_latest_commit — engine 接入 context commit 系统的统一入口.

每 turn 的 prepare 阶段调一次。多 agent 并发安全: 通过
``load_commit_for_head`` 按当前 branch head 拿"我这条分支"的 parent
commit, 而不是全局 latest。两个 agent 在不同分支上同时跑互不干扰 —
新 commit 各自挂在各自 parent 下, 文件 (uuid hex 命名) 不撞, 无需锁。

逻辑:
  1. 按 ``head_node_id`` 找到本分支祖先链上最近的 commit。
  2. 如果它的 head_node_id 跟当前 head 一致 → 直接返回。
  3. 否则计算 delta: history 里跟 commit 不重合的新节点, 调
     ``generate_commit`` 产新 commit。
  4. 该分支没 commit (cold start) → 把全部 history 当 new_nodes 喂
     给 generator 一次性建链头。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .types import ContextCommit
from .store import load_commit_for_head
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
    # Per-branch parent lookup. ``load_commit_for_head`` walks the DAG
    # ancestor chain from head_node_id and returns the most-recent
    # commit whose head sits on this branch. Concurrency-safe: two
    # agents on different branches resolve to different parents and
    # never share write targets.
    latest = load_commit_for_head(store, session_id, head_node_id)

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
