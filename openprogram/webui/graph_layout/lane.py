"""Lane (branch column) assignment for the session DAG.

Rule (docs/design/runtime/dag-layout-algorithm.md §2): count branches,
number them in the order they appear (by seq), starting from 0. No
"trunk" special case, no zeroing.

A branch = a conversation chain. Walk from a start node down the
``caller`` sub-call tree AND the ``predecessor`` conversation chain,
claiming every node into the same lane. At a fork (same predecessor
with >1 child), the first sibling continues the lane; each later
sibling starts a NEW branch → next lane number.

Lane numbers are assigned in seq order of branch starts:
  * ROOT (and the trunk hanging off it) → lane 0
  * each fork's later siblings → next lane, in the order they appear
"""
from __future__ import annotations

from typing import Optional

from .topology import build_maps
from ._common import is_root, ts, predecessor_of as pred_of


class LaneAllocator:
    def __init__(self) -> None:
        self._next = 0

    def alloc(self) -> int:
        i = self._next
        self._next += 1
        return i

    @property
    def used(self) -> int:
        return self._next


def compute_lane(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
    fork_siblings: dict[str, list[str]],
    head_id: Optional[str] = None,
) -> tuple[dict[str, int], LaneAllocator]:
    lane: dict[str, int] = {}
    alloc = LaneAllocator()

    caller_children, pred_children, _ = build_maps(by_id)

    # A fork = a predecessor with >1 child. The first sibling (by seq)
    # continues its parent's lane; later siblings each begin a NEW branch.
    # ``is_fork_continuation`` excludes those later siblings from same-lane
    # claiming — they get their own lane in the branch-start pass.
    is_fork_continuation: dict[str, bool] = {}
    for _pid, kids in fork_siblings.items():
        if len(kids) > 1:
            for i, k in enumerate(kids):
                is_fork_continuation[k] = (i == 0)

    def _same_lane(kid: str) -> bool:
        """A child stays in the parent's lane unless it's a later fork
        sibling (those start a new branch)."""
        return is_fork_continuation.get(kid, True)

    def _claim(start: str, my_lane: int) -> None:
        """Paint start + its same-branch descendants into my_lane.

        A branch follows the ``predecessor`` (conversation) chain — a
        node continuing the chat stays in its predecessor's lane. The
        ``caller`` edge is followed ONLY for sub-calls (tool/code nodes
        invoked inside a turn), which live in the same lane as their
        caller. We do NOT follow caller for top-level conversation nodes
        (user/llm whose caller is ROOT) — their lane comes from their
        predecessor, not from ROOT, so a fork-continuation user doesn't
        get yanked back to lane 0.

        A later fork sibling (same predecessor, not the first) is NOT
        claimed here — it begins its own branch/lane.
        """
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in lane:
                continue
            lane[cur] = my_lane
            # Sub-calls: caller children that are NOT top-level conv
            # nodes (i.e. their lane really is defined by the caller).
            for kid in caller_children.get(cur, []):
                if kid in lane or not _same_lane(kid):
                    continue
                # A node with its own predecessor belongs to that
                # conversation chain — claim it via predecessor, not here.
                kid_pred = pred_of(by_id, by_id[kid])
                if kid_pred:
                    continue
                # spawn 分支根不是 turn 内 sub-call：它开启新分支，第 2 趟
                # 扫描时自取新 lane（dag-rendering.md 场景 10）。
                if by_id[kid].get("source") == "agent_spawn":
                    continue
                stack.append(kid)
            # Conversation continuation: predecessor children stay in lane
            # (first fork sibling continues; later ones start new lanes).
            for kid in pred_children.get(cur, []):
                if kid not in lane and _same_lane(kid):
                    stack.append(kid)

    def _seq(nid: str) -> float:
        return ts(by_id, nid)

    # 1) trunk roots first (ROOT / display=root) → lane 0
    roots = sorted((nid for nid, m in by_id.items() if is_root(m)), key=_seq)
    for r in roots:
        if r not in lane:
            _claim(r, alloc.alloc())

    # 2) remaining nodes in seq order. Each unclaimed node either
    #    continues an already-laned predecessor (inherit its lane) or
    #    starts a fresh branch (new lane). Processing in seq order means
    #    a continuation is reached after its predecessor is laned.
    for nid in sorted(by_id, key=_seq):
        if nid in lane:
            continue
        pred = pred_of(by_id, by_id[nid])
        if pred and pred in lane and _same_lane(nid):
            _claim(nid, lane[pred])
        else:
            _claim(nid, alloc.alloc())

    return lane, alloc
