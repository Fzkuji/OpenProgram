"""Strip nodes that aren't part of the visible DAG.

Microcompact (``openprogram/context/microcompact``) writes a synthetic
``summary_<hex>`` LLM node plus a parallel ``k_<hex>`` user/assistant
chain holding the compacted rewrite of the conversation. In the DAG
those nodes form a second root that visually mirrors the trunk —
correct as data, confusing as a graph because the user sees the same
conversation twice. They get filtered out of the layout input.

Filtering at the layout boundary keeps the persistence layer
authoritative (the rewrite IS in the DB, it's just not painted).
"""
from __future__ import annotations


def _is_microcompact_synthetic(node_id: str) -> bool:
    return node_id.startswith("summary_") or node_id.startswith("k_")


def _is_task_followup_user(node: dict) -> bool:
    """``[系统消息]…`` user msg that runner writes after a /task --async
    sub-agent finishes. It's a synthetic trigger so the parent LLM
    has a user_msg to react to — chat hides it (display=runtime) and
    the DAG shouldn't paint it either.

    See docs/design/runtime/dag-node-model.md (the "合成桥不是合法节点" rule).
    """
    return (
        node.get("source") == "task_followup"
        and node.get("role") == "user"
    )


def normalize_followup(graph_entries: list[dict]) -> list[dict]:
    """Re-parent task_followup assistant replies onto their sibling
    attach pointer so the auto-followup turn appears as
    ``attach → reply`` in the sequence chain. Then the synthetic
    user_msg can be filtered out without breaking conv linkage.

    Mutates the dicts in place (matches the rest of the layout
    pipeline's "annotate the graph_entries it was given" contract).
    """
    by_id = {m["id"]: m for m in graph_entries if m.get("id")}
    # caller_msg_id → attach pointer id (one per caller in practice)
    attach_by_caller: dict[str, str] = {}
    for nid, node in by_id.items():
        if node.get("function") != "attach":
            continue
        caller = node.get("predecessor") or node.get("caller")
        if caller:
            attach_by_caller[caller] = nid
    # Find task_followup user msgs; redirect their replies' predecessor
    for nid, node in by_id.items():
        if not _is_task_followup_user(node):
            continue
        followup_user_parent = node.get("predecessor")
        attach_id = attach_by_caller.get(followup_user_parent or "")
        # 没有 attach 指针的 followup（异步回流路径可能不写 attach）：
        # 把 reply 直接挂回收到回流的那轮，否则合成 user 被过滤后 reply
        # 的 predecessor 悬空，成为 depth=0 的孤儿根、飘到 ROOT 行。
        if not attach_id and followup_user_parent in by_id:
            attach_id = followup_user_parent
        if not attach_id:
            continue
        # Reply's schema predecessor == followup user msg id; rewrite
        # to point at the attach pointer instead.
        for other_id, other in by_id.items():
            if (
                other.get("source") == "task_followup"
                and other.get("role") == "assistant"
                and other.get("predecessor") == nid
            ):
                other["predecessor"] = attach_id
    return graph_entries


def filter_visible(graph_entries: list[dict]) -> list[dict]:
    """Return the subset of nodes that should appear in the DAG.

    Mutates nothing — caller passes the result downstream. We keep
    this pure so the layout pipeline can be unit-tested without DB.

    Run ``normalize_followup`` *before* this so the conv chain is
    intact after the synthetic user_msg is stripped.
    """
    return [
        m for m in graph_entries
        if m.get("id")
        and not _is_microcompact_synthetic(m["id"])
        and not _is_task_followup_user(m)
        and m.get("display") != "runtime"
    ]
