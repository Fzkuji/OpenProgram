"""Strip nodes that aren't part of the visible DAG.

Compaction summary nodes are ordinary chain members (``role=llm``,
``metadata.covers``) and ARE painted — they're a real event in the
conversation. Only genuinely synthetic bridges (task-followup triggers,
``display=runtime`` rows) get filtered here.

Filtering at the layout boundary keeps the persistence layer
authoritative.
"""
from __future__ import annotations


def _is_job_followup_user(node: dict) -> bool:
    """``[系统消息]…`` user msg that runner writes after a /task --async
    sub-agent finishes. It's a synthetic trigger so the parent LLM
    has a user_msg to react to — chat hides it (display=runtime) and
    the DAG shouldn't paint it either.

    See docs/design/runtime/dag-node-model.md (the "合成桥不是合法节点" rule).
    """
    return (
        node.get("source") == "job_followup"
        and node.get("role") == "user"
    )


def normalize_followup(graph_entries: list[dict]) -> list[dict]:
    """Re-parent job_followup assistant replies onto the turn that
    received the merge-back — the synthetic user msg's own predecessor
    — so the synthetic user can be filtered out without breaking conv
    linkage.

    NEVER onto the attach pointer: attach pointers are ``display=
    runtime`` rows that ``filter_visible`` strips from the graph, so a
    reply chained to one has a predecessor that exists nowhere in the
    payload. The frontend's edge walk dead-ends on it and the reply
    draws as an orphan floating off every chain — two of those, from
    one real session, are what this rule was rewritten from.

    Mutates the dicts in place (matches the rest of the layout
    pipeline's "annotate the graph_entries it was given" contract).
    """
    by_id = {m["id"]: m for m in graph_entries if m.get("id")}
    for nid, node in by_id.items():
        if not _is_job_followup_user(node):
            continue
        followup_user_parent = node.get("predecessor")
        if not followup_user_parent or followup_user_parent not in by_id:
            continue
        # Reply's schema predecessor == followup user msg id; rewrite
        # to skip the about-to-be-filtered synthetic user.
        for other in by_id.values():
            if (
                other.get("source") == "job_followup"
                and other.get("role") == "assistant"
                and other.get("predecessor") == nid
            ):
                other["predecessor"] = followup_user_parent
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
        and not _is_job_followup_user(m)
        and m.get("display") != "runtime"
    ]
