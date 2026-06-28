"""Unified graph builder for the DAG viewport.

Single entry point: ``build_session_graph(session_id, head_id)``
returns the annotated graph node list. Both ``session.py`` and
``branch.py`` call this instead of duplicating the construction.
"""
from __future__ import annotations

from typing import Any, Optional


def build_session_graph(
    session_id: str,
    head_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Build the annotated DAG graph for a session.

    Returns a list of graph node dicts with ``_tier``, ``_depth``,
    ``_lane`` computed by ``graph_layout.annotate_graph``.
    """
    from openprogram.agent.session_db import default_db
    from openprogram.webui.ws_actions.branch import (
        _attach_info,
        _attach_embed_stats,
        _extract_tool_input,
        _extract_function_name,
        _extract_tool_is_error,
        _extract_llm_meta,
        _extract_attach_label,
    )
    from openprogram.webui.graph_layout import annotate_graph

    db = default_db()

    try:
        full_msgs = db.get_messages(session_id) or []
    except Exception:
        full_msgs = []

    called_by_map: dict[str, str] = {}
    nodes = []
    try:
        nodes = db.get_nodes(session_id) or []
        for n in nodes:
            if n.called_by:
                called_by_map[n.id] = n.called_by
    except Exception:
        pass

    graph: list[dict[str, Any]] = []

    root_node = next(
        (n for n in nodes if (n.metadata or {}).get("display") == "root"),
        None,
    ) if nodes else None
    if root_node:
        graph.append({
            "id": root_node.id,
            "called_by": "",
            "caller": "",
            "role": "user",
            "display": "root",
            "preview": "ROOT",
        })

    for m in full_msgs:
        content = m.get("content") or ""
        preview = content.strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "…"
        aref, amanual, asrc_commit = _attach_info(m)
        aembed_n, aembed_tok = _attach_embed_stats(db, session_id, asrc_commit)
        mid = m.get("id") or ""
        graph.append({
            "id": mid,
            "called_by": m.get("called_by"),
            "caller": called_by_map.get(mid, "") or m.get("caller") or "",
            "role": m.get("role"),
            "function": m.get("function"),
            "display": m.get("display"),
            "source": m.get("source"),
            "preview": preview,
            "input": _extract_tool_input(m),
            "name": _extract_function_name(m),
            "is_error": _extract_tool_is_error(m),
            "llm": _extract_llm_meta(m),
            "created_at": m.get("created_at"),
            "attach_ref": aref,
            "attach_manual": amanual,
            "attach_label": _extract_attach_label(m),
            "attach_source_commit_id": asrc_commit,
            "attach_embed_count": aembed_n,
            "attach_embed_tokens": aembed_tok,
        })

    return annotate_graph(graph, head_id)
