"""Unified graph builder for the DAG viewport.

Single entry point: ``build_session_graph(session_id, head_id)``
returns the annotated graph node list. Both ``session.py`` and
``branch.py`` call this instead of duplicating the construction.
"""
from __future__ import annotations

import json
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
        _extract_attach_session_id,
        _is_merge_temp_attach,
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
    caller_map: dict[str, str] = {}
    nodes = []
    try:
        nodes = db.get_nodes(session_id) or []
        hidden_ids = {
            n.id for n in nodes
            if (n.metadata or {}).get("execution_control")
        }
        changed = True
        while changed:
            before = len(hidden_ids)
            hidden_ids.update(
                n.id for n in nodes if n.caller in hidden_ids
            )
            changed = len(hidden_ids) != before
        nodes = [n for n in nodes if n.id not in hidden_ids]
        full_msgs = [
            m for m in full_msgs if m.get("id") not in hidden_ids
        ]
        for n in nodes:
            if n.caller:
                caller_map[n.id] = n.caller
    except Exception:
        pass

    # ``list_branches`` only returns live tips, so a retired sub-agent
    # branch — every completed one, since the runner marks it merged —
    # would lose its name on the way out. Read the meta dict directly and
    # stamp ``branch_name`` on the anchor node, which is what lets the DAG
    # label a sub-agent capsule for a session that ran before the runner
    # started writing labels onto attach pointers.
    branch_names: dict[str, str] = {}
    try:
        pair = db._open(session_id)  # noqa: SLF001
        if pair is not None:
            _git, _idx = pair
            for anchor, info in (_idx.meta.get("branches") or {}).items():
                name = (info or {}).get("name") if isinstance(info, dict) else info
                if isinstance(name, str) and name.strip():
                    branch_names[anchor] = name.strip()
    except Exception:
        pass

    # Compaction summaries carry ``metadata.covers_ids`` — the exact
    # chain nodes the summary replaces, written by the persister
    # (context/persistence.py). Seq intervals span sibling branches in
    # a DAG, so ids are the only faithful record; the graph adds the
    # caller subtrees hanging off those turns (a covered turn folds
    # with its calls) and does no seq arithmetic at all.
    # Compaction is a ROLLING summary: each new summary absorbs the
    # previous one's text (context/engine.py chains previous_summary)
    # and ``extra_meta._last_summary_id`` points at the only one the
    # next request carries. Superseded summaries are inert relics —
    # they keep their rows but must not fold anything, or two capsules
    # fight over the same range.
    covers_ids: dict[str, list[str]] = {}
    superseded: set[str] = set()
    if nodes:
        seq_of = {n.id: n.seq for n in nodes}
        children_of: dict[str, list[str]] = {}
        for m in nodes:
            if m.caller:
                children_of.setdefault(m.caller, []).append(m.id)
        for n in nodes:
            raw = (n.metadata or {}).get("covers_ids")
            if not isinstance(raw, (list, tuple)) or not raw:
                continue
            spine = [str(x) for x in raw
                     if str(x) in seq_of and str(x) != n.id]
            keep = set(spine)
            stack = list(spine)
            while stack:
                for cid in children_of.get(stack.pop(), ()):  # noqa: B909
                    if cid not in keep:
                        keep.add(cid)
                        stack.append(cid)
            covered = sorted(keep, key=lambda i: seq_of[i])
            if covered:
                covers_ids[n.id] = covered
        if len(covers_ids) > 1:
            active = ""
            try:
                extra = (db.get_session(session_id) or {}).get(
                    "extra_meta") or {}
                active = extra.get("_last_summary_id") or ""
            except Exception:
                pass
            if active not in covers_ids:
                # No stamp (older data): the newest summary is the one
                # the rolling chain ends on.
                active = max(covers_ids, key=lambda i: seq_of[i])
            superseded = set(covers_ids) - {active}
            for sid_old in superseded:
                covers_ids.pop(sid_old, None)

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
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        preview = content.strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "…"
        aref, amanual, asrc_commit = _attach_info(m)
        attach_session_id = _extract_attach_session_id(m)
        aembed_n, aembed_tok = _attach_embed_stats(
            db, attach_session_id or session_id, asrc_commit,
        )
        mid = m.get("id") or ""
        row = {
            "id": mid,
            "predecessor": m.get("predecessor"),
            "caller": caller_map.get(mid, "") or m.get("caller") or "",
            "role": m.get("role"),
            "function": m.get("function"),
            "display": m.get("display"),
            "source": m.get("source"),
            "status": m.get("status"),
            "preview": preview,
            "input": _extract_tool_input(m),
            "name": _extract_function_name(m),
            "is_error": _extract_tool_is_error(m),
            "llm": _extract_llm_meta(m),
            "created_at": m.get("created_at"),
            "attach_ref": aref,
            "attach_manual": amanual,
            "attach_merge_temp": _is_merge_temp_attach(m),
            "attach_label": _extract_attach_label(m),
            "attach_source_commit_id": asrc_commit,
            "attach_session_id": attach_session_id,
            "attach_embed_count": aembed_n,
            "attach_embed_tokens": aembed_tok,
        }
        if mid in covers_ids:
            row["covers_ids"] = covers_ids[mid]
        if mid in superseded:
            row["superseded_summary"] = True
        for key in (
            "tokens_before", "tokens_after",
            "summarised_count", "compacted_at",
        ):
            if m.get(key) is not None:
                row[key] = m[key]
        if mid in branch_names:
            row["branch_name"] = branch_names[mid]
        spawned_from_session = m.get("spawned_from_session")
        if (
            m.get("source") == "agent_spawn"
            and isinstance(spawned_from_session, str)
            and spawned_from_session
            and spawned_from_session != session_id
        ):
            row["spawn_remote"] = True
            row["spawn_remote_session"] = spawned_from_session
            row["spawn_remote_id"] = row.get("caller") or ""
            # ``caller`` is namespaced by spawn_remote_session and must not
            # accidentally bind to an equal node id in the target graph.
            # Keep the remote id above and normalize the local drawing edge.
            if root_node is not None:
                row["caller"] = root_node.id
        graph.append(row)

    # attach 指针不画节点（rendering.md 场景 8/10），但回流长虚线需要
    # 它携带的 ref：把 ref 戳到嵌入位置（attach 的 predecessor 节点）上，
    # 前端从子分支 tip 画回这里。attach 行本身随 display=runtime 被过滤。
    by_id_row = {n["id"]: n for n in graph}
    for n in graph:
        if n.get("function") != "attach":
            continue
        host = by_id_row.get(n.get("predecessor") or "")
        if host is None:
            continue
        attach_session_id = n.get("attach_session_id")
        if attach_session_id and attach_session_id != session_id:
            if not n.get("attach_manual") and not n.get("attach_merge_temp"):
                host["spawn_out"] = True
                host["spawn_out_session"] = attach_session_id
                if n.get("attach_ref"):
                    host["spawn_out_head"] = n["attach_ref"]
            # Every cross-session attach endpoint belongs to another graph,
            # so none of them can produce an in-session return edge. Only
            # agent-spawn pointers additionally mark the source node.
            continue
        if not n.get("attach_ref"):
            continue
        host.setdefault("attach_returns", []).append(n["attach_ref"])

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
                # 跨会话 spawn 根：caller 在另一个会话的图里。挂回 ROOT
                # 之前打标，前端画 ↗ 角标（rendering.md 第四节徽标）。
                if (
                    n.get("source") == "agent_spawn"
                    and caller
                    and not n.get("spawn_remote")
                ):
                    n["spawn_remote"] = True
                    n["spawn_remote_id"] = caller
                n["caller"] = rid

    return annotate_graph(graph, head_id)
