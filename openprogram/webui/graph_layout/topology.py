"""Build adjacency maps from called_by edges.

Two maps:
  * ``call_children[caller_id]`` — nodes whose called_by points here
  * ``fork_siblings[called_by]`` — nodes sharing the same called_by
    (for branch/fork detection)
"""
from __future__ import annotations

from ._common import called_by_of, called_by_of, ts


def build_children(
    by_id: dict[str, dict],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    call_children: dict[str, list[str]] = {}
    fork_siblings: dict[str, list[str]] = {}
    for nid, m in by_id.items():
        ca = called_by_of(by_id, m)
        if ca:
            call_children.setdefault(ca, []).append(nid)
        pid = called_by_of(by_id, m)
        if pid:
            fork_siblings.setdefault(pid, []).append(nid)
    for kids in call_children.values():
        kids.sort(key=lambda x: ts(by_id, x))
    for kids in fork_siblings.values():
        kids.sort(key=lambda x: ts(by_id, x))
    return call_children, fork_siblings
