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


def filter_visible(graph_entries: list[dict]) -> list[dict]:
    """Return the subset of nodes that should appear in the DAG.

    Mutates nothing — caller passes the result downstream. We keep
    this pure so the layout pipeline can be unit-tested without DB.
    """
    return [
        m for m in graph_entries
        if m.get("id") and not _is_microcompact_synthetic(m["id"])
    ]
