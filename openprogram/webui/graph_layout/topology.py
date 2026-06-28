"""Build adjacency maps from predecessor edges.

Two maps:
  * ``call_children[caller_id]`` — nodes whose predecessor points here
  * ``fork_siblings[predecessor]`` — nodes sharing the same predecessor
    (for branch/fork detection)
"""
from __future__ import annotations

from ._common import predecessor_of, ts


def build_children(
    by_id: dict[str, dict],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    call_children: dict[str, list[str]] = {}
    fork_siblings: dict[str, list[str]] = {}
    for nid, m in by_id.items():
        ca = predecessor_of(by_id, m)
        if ca:
            call_children.setdefault(ca, []).append(nid)
        pid = predecessor_of(by_id, m)
        if pid:
            fork_siblings.setdefault(pid, []).append(nid)
    for kids in call_children.values():
        kids.sort(key=lambda x: ts(by_id, x))
    for kids in fork_siblings.values():
        kids.sort(key=lambda x: ts(by_id, x))
    return call_children, fork_siblings
