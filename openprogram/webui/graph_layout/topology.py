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

from ._common import predecessor_of, caller_of, ts


def build_maps(
    by_id: dict[str, dict],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """Return ``(caller_children, pred_children, fork_siblings)``."""
    caller_children: dict[str, list[str]] = {}
    pred_children: dict[str, list[str]] = {}
    fork_siblings: dict[str, list[str]] = {}
    for nid, m in by_id.items():
        c = caller_of(by_id, m)
        if c:
            caller_children.setdefault(c, []).append(nid)
        p = predecessor_of(by_id, m)
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
