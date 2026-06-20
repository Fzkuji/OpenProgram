"""Build adjacency maps from the two edge types.

Two children dictionaries, mutually exclusive per child:

  * ``conv_children[parent_id]`` — conv-edge kids (next user/assistant
    turns). Multiple entries = retry siblings.
  * ``call_children[caller_id]`` — sub-call kids (tool / FunctionCall
    / sub-LLM). Many per assistant turn is the common case.

Both are sorted by ``created_at`` so "first conv-child" / "k-th
sub-call" definitions are stable regardless of insertion order.
"""
from __future__ import annotations

from ._common import ts, caller_of, conv_parent_of


def build_children(
    by_id: dict[str, dict],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    conv_children: dict[str, list[str]] = {}
    call_children: dict[str, list[str]] = {}
    for nid, m in by_id.items():
        cp = conv_parent_of(by_id, m)
        if cp:
            conv_children.setdefault(cp, []).append(nid)
        ca = caller_of(by_id, m)
        if ca:
            call_children.setdefault(ca, []).append(nid)
        # ROOT's children (user nodes) also register as conv-children
        # of their parent_id target — so fork detection (two user nodes
        # branching from the same llm reply) still works.
        if ca and (by_id.get(ca, {}).get("display") == "root"):
            pid = m.get("parent_id")
            if pid and pid in by_id and pid != nid:
                conv_children.setdefault(pid, []).append(nid)
    for kids in conv_children.values():
        kids.sort(key=lambda x: ts(by_id, x))
    for kids in call_children.values():
        kids.sort(key=lambda x: ts(by_id, x))
    return conv_children, call_children
