"""Persist the exact tool classification used by a provider request."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from openprogram.context.system_prompt_node import (
    _active_head_id,
    _branch_anchor_ids,
    _id_of,
    _legacy_snapshot_anchors,
    _snapshot_on_branch,
)

NODE_NAME = "context/tool_snapshot"


def latest_recorded_tool_snapshot(
    store: Any,
    session_id: str,
    head_id: str | None = None,
) -> Optional[dict]:
    try:
        nodes = store.get_nodes(session_id) or []
    except Exception:
        return None
    branch_anchors = _branch_anchor_ids(store, session_id, head_id)
    if branch_anchors == set():
        return None
    legacy_anchors = _legacy_snapshot_anchors(nodes)
    for node in reversed(nodes):
        if (_name_of(node) != NODE_NAME
                or not _snapshot_on_branch(
                    node,
                    branch_anchors,
                    legacy_anchor=legacy_anchors.get(_id_of(node)),
                )):
            continue
        try:
            value = json.loads(_output_of(node))
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return None


def record_tool_snapshot(
    store: Any,
    session_id: str | None,
    tools: list[Any],
) -> Optional[str]:
    """Record priced loaded/deferred rows after the turn toolset is frozen."""
    if not session_id:
        return None
    try:
        from openprogram.context.budget import estimate_tools_breakdown

        rows = []
        for tool, estimate in zip(tools, estimate_tools_breakdown(tools)):
            rows.append({
                "name": estimate.get("name") or getattr(tool, "name", "") or "",
                "tokens": int(estimate.get("tokens") or 0),
                "deferred": bool(estimate.get("deferred")),
                "server": str(getattr(tool, "_mcp_server", None) or ""),
            })
        payload = json.dumps({"tools": rows}, ensure_ascii=False, sort_keys=True)
        anchor_head_id = _active_head_id(store, session_id)
        previous = latest_recorded_tool_snapshot(
            store, session_id, anchor_head_id,
        )
        if previous is not None:
            old = json.dumps(previous, ensure_ascii=False, sort_keys=True)
            if old == payload:
                return None

        from openprogram.context.nodes import Call, ROLE_CODE
        from openprogram.store import SessionNodeWriter

        node = Call(
            id="ctxtools_" + uuid.uuid4().hex[:10],
            created_at=time.time(),
            role=ROLE_CODE,
            name=NODE_NAME,
            output=payload,
            caller="ROOT",
            predecessor=None,
            metadata={
                "display": "runtime",
                "anchor_head_id": anchor_head_id or "",
            },
        )
        SessionNodeWriter(store, session_id).append(node)
        return node.id
    except Exception:
        return None


def _name_of(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("name") or node.get("function") or "")
    return str(getattr(node, "name", "") or "")


def _output_of(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("output") or node.get("content") or "")
    return str(getattr(node, "output", "") or "")


__all__ = ["NODE_NAME", "latest_recorded_tool_snapshot", "record_tool_snapshot"]
