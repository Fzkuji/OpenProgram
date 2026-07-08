"""Depth (row index) per node = 到根的层级跳数（conversation level）。

高度语义按对话轮次对齐，而非 DFS 访问序：
  * 根节点 depth 0（最高）；
  * 每个节点 depth = 其结构父（对话前驱 predecessor，缺失时用 caller
    挂根/子调用父）的 depth + 1；
  * 于是「同一轮 / 同一分叉层」的节点自然落在同一高度，不会出现分支首
    节点和根平行的情况。

这样 y 坐标直接反映时间深度：一次问答两层、多条分支的首轮全部同高。
"""
from __future__ import annotations

from ._common import predecessor_of, caller_of


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
    fork_siblings: dict[str, list[str]] | None = None,
) -> dict[str, float]:
    """每个节点的 depth = 到根的结构父跳数。call_children / fork_siblings
    保留在签名里兼容调用方，本层级算法只需 by_id 上的 predecessor/caller。"""

    def _parent(nid: str) -> str | None:
        m = by_id.get(nid)
        if m is None:
            return None
        # 对话前驱优先，没有则 caller（分支首节点靠 caller=ROOT 挂根，
        # turn 内 sub-call 靠 caller 指向其 llm）。只有指向图内节点才算父。
        p = predecessor_of(by_id, m) or caller_of(by_id, m)
        return p if (p and p in by_id) else None

    depth: dict[str, float] = {}

    def _depth(nid: str, stack: frozenset[str]) -> float:
        cached = depth.get(nid)
        if cached is not None:
            return cached
        if nid in stack:  # 防御性防环（正常 DAG 不会成环）
            return 0.0
        p = _parent(nid)
        d = 0.0 if p is None else _depth(p, stack | {nid}) + 1.0
        depth[nid] = d
        return d

    for nid in by_id:
        _depth(nid, frozenset())

    return depth
