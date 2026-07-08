"""Build adjacency maps from the two node edges.

Three maps (see docs/design/runtime/dag-layout-algorithm.md):
  * ``caller_children[caller]`` — nodes whose ``caller`` points here
    (sub-call tree: ROOT→user→llm→tool, function internals)
  * ``pred_children[pred]``     — nodes whose ``predecessor`` points here
    (conversation chain: this turn's reply, next turn's user)
  * ``fork_siblings[pred]``     — nodes sharing the same ``predecessor``
    (fork detection: same pred with >1 child = a fork)
"""
from __future__ import annotations

from ._common import predecessor_of, caller_of, ts, is_root


def build_maps(
    by_id: dict[str, dict],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """Return ``(caller_children, pred_children, fork_siblings)``."""
    caller_children: dict[str, list[str]] = {}
    pred_children: dict[str, list[str]] = {}
    fork_siblings: dict[str, list[str]] = {}
    for nid, m in by_id.items():
        c = caller_of(by_id, m)
        p = predecessor_of(by_id, m)
        if c:
            caller_children.setdefault(c, []).append(nid)
            # ROOT 的 caller children 里，**没有对话前驱**的才是从根新分出的
            # 分支根（并列兄弟），应像 predecessor fork 一样横向各占 lane。
            # 有 predecessor 的节点是某条分支的**续聊**（如在 A 分支的
            # reply 后继续问），即便建库时 caller 也写了 ROOT，它也该跟着
            # predecessor 待在原分支 lane，而不是被误当成新分支拉走。
            # turn 内 sub-call（caller 指向 llm，非 root）同样不在此列。
            if is_root(by_id.get(c) or {}) and not (p and p in by_id):
                fork_siblings.setdefault(c, []).append(nid)
        if p:
            pred_children.setdefault(p, []).append(nid)
            fork_siblings.setdefault(p, []).append(nid)
    for kids in caller_children.values():
        kids.sort(key=lambda x: ts(by_id, x))
    for kids in pred_children.values():
        kids.sort(key=lambda x: ts(by_id, x))
    for kids in fork_siblings.values():
        kids.sort(key=lambda x: ts(by_id, x))
    return caller_children, pred_children, fork_siblings


def build_children(
    by_id: dict[str, dict],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Backward-compat: ``(call_children, fork_siblings)``.

    ``call_children`` = caller children ∪ predecessor children — every
    node hanging under a node (sub-calls + conversation continuation),
    used by depth/lane DFS walks.
    """
    caller_children, pred_children, fork_siblings = build_maps(by_id)
    call_children: dict[str, list[str]] = {}
    for parent, kids in caller_children.items():
        call_children.setdefault(parent, []).extend(kids)
    for parent, kids in pred_children.items():
        bucket = call_children.setdefault(parent, [])
        for k in kids:
            if k not in bucket:
                bucket.append(k)
    for kids in call_children.values():
        kids.sort(key=lambda x: ts(by_id, x))
    return call_children, fork_siblings
