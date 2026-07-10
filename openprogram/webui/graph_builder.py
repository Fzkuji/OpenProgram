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

    # Named branches: {branch_anchor_id: human name}. meta.json's
    # `branches` dict is keyed by the branch anchor node id (the branch's
    # first-turn reply); stamp the name onto that node so the DAG can
    # label which branch is which. Includes merged branches (unlike
    # list_branches, which only returns live tips). Unnamed branches
    # (no `name`) get no label.
    # Named branches from the session meta — INCLUDING merged ones, which
    # list_branches drops but whose lanes stay in the graph. The frontend
    # badge renderer unions these with the live rows so merged branches
    # keep their name label. Keys are branch anchor node ids.
    branch_names: dict[str, str] = {}
    try:
        sess = db.get_session(session_id) or {}
        for anchor_id, info in (sess.get("branches") or {}).items():
            name = (info or {}).get("name")
            if name:
                branch_names[anchor_id] = name
    except Exception:
        pass

    caller_map: dict[str, str] = {}
    nodes = []
    try:
        nodes = db.get_nodes(session_id) or []
        for n in nodes:
            if n.caller:
                caller_map[n.id] = n.caller
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
            "predecessor": "",
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
            "predecessor": m.get("predecessor"),
            "caller": caller_map.get(mid, "") or m.get("caller") or "",
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
            "branch_name": branch_names.get(mid),
            "attach_source_commit_id": asrc_commit,
            "attach_embed_count": aembed_n,
            "attach_embed_tokens": aembed_tok,
        })

    # Root 兜底：部分分支的首节点建库时 predecessor 与 caller 都没写
    # （历史数据 / 某些开分支路径），下发后既没有对话前驱也没有子调用父，
    # 在 DAG 里会各自成为孤儿根，渲染成互不连通的多棵树、且 ROOT 子树悬空。
    # 这里把「非 root、且 predecessor/caller 都不指向图内任何节点」的顶层
    # 节点挂回 ROOT，让所有分支归到同一棵树。只补真孤儿，对已有 caller=ROOT
    # 或有 predecessor 的节点无副作用。
    if root_node:
        ids = {n["id"] for n in graph}
        rid = root_node.id
        for n in graph:
            if n["id"] == rid or n.get("display") == "root":
                continue
            pred = n.get("predecessor")
            caller = n.get("caller")
            pred_in = bool(pred) and pred in ids
            caller_in = bool(caller) and caller in ids
            # 既无有效前驱又无有效调用父的顶层节点 → 挂回 ROOT。
            # （predecessor 指向图外的 spawn/followup reply 不在此列，交由
            #  normalize_followup 处理，避免干扰 task-followup 的边重写。）
            if not pred_in and not caller_in:
                n["caller"] = rid

    return annotate_graph(graph, head_id)
