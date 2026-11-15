"""Strip nodes that aren't part of the visible DAG.

Compaction summary nodes are ordinary chain members (``role=llm``,
``metadata.covers``) and ARE painted — they're a real event in the
conversation. Only genuinely synthetic bridges (task-followup triggers,
``display=runtime`` rows) get filtered here.

Filtering at the layout boundary keeps the persistence layer
authoritative.
"""
from __future__ import annotations


# Runtime notices the agent reacts to without the user typing anything:
# an async sub-agent finishing (job_followup) and a background command it
# left running ending (process_exit). Each writes a synthetic user msg so
# the LLM has something to answer, then the reply.
FOLLOWUP_SOURCES = frozenset({"job_followup", "process_exit"})


def _is_followup_user(node: dict) -> bool:
    """The synthetic trigger of a runtime notice. Chat hides it
    (display=runtime) and the DAG shouldn't paint it either.

    See docs/reference/design/runtime/dag-node-model.md (the "合成桥不是合法节点" rule).
    """
    return (
        node.get("source") in FOLLOWUP_SOURCES
        and node.get("role") == "user"
    )


def _is_hidden(node: dict) -> bool:
    """A row ``filter_visible`` strips: a runtime-notice trigger or any
    ``display=runtime`` row (attach pointers, the synthetic "continue the
    interrupted task" turn a restart writes)."""
    return _is_followup_user(node) or node.get("display") == "runtime"


def bridge_hidden(graph_entries: list[dict]) -> list[dict]:
    """Re-parent every node whose conversation predecessor is about to be
    filtered onto that node's nearest surviving predecessor, so stripping
    hidden rows never breaks the conv chain.

    A runtime-notice reply lands on the turn that received the notice
    (the synthetic user msg's own predecessor) — NEVER on the attach
    pointer beside it. A restart's resumed reply lands on the turn the
    interrupted run was answering. Either way the predecessor ends up on
    a node present in the payload; one left pointing at a stripped row
    dead-ends the frontend's edge walk and the whole branch below it
    draws as an orphan floating off every chain.

    A reply bridged over a restart turn (``trigger="restart"``) is also
    stamped ``resumed``: it is the rest of the reply the restart cut off,
    so the frontend merges it into the same anchor as a notice reply
    (``isFollowup`` in ``passes/thread-nodes.ts``), the way the chat
    draws it as one reply.

    A walk that leaves the graph (the hidden row's own predecessor is
    absent) leaves the node alone. Mutates the dicts in place (matches
    the rest of the layout pipeline's "annotate the graph_entries it was
    given" contract).
    """
    by_id = {m["id"]: m for m in graph_entries if m.get("id")}

    def _visible_ancestor(nid: str) -> tuple[str | None, bool]:
        seen: set[str] = set()
        restart = False
        while nid in by_id and _is_hidden(by_id[nid]):
            if nid in seen:
                return None, False
            seen.add(nid)
            restart = restart or by_id[nid].get("trigger") == "restart"
            nid = by_id[nid].get("predecessor") or ""
        return (nid, restart) if nid in by_id else (None, False)

    for node in by_id.values():
        pred = node.get("predecessor")
        if not pred or pred not in by_id or not _is_hidden(by_id[pred]):
            continue
        target, restart = _visible_ancestor(pred)
        if target and target != node.get("id"):
            node["predecessor"] = target
            if restart and node.get("role") == "assistant":
                node["resumed"] = True
    return graph_entries


def filter_visible(graph_entries: list[dict]) -> list[dict]:
    """Return the subset of nodes that should appear in the DAG.

    Mutates nothing — caller passes the result downstream. We keep
    this pure so the layout pipeline can be unit-tested without DB.

    Run ``bridge_hidden`` *before* this so the conv chain is
    intact after the synthetic user_msg is stripped.
    """
    return [
        m for m in graph_entries
        if m.get("id") and not _is_hidden(m)
    ]
