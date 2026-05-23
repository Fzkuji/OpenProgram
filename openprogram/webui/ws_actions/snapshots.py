"""Snapshot timeline WS actions: list_snapshots / get_snapshot_detail.

Exposes the per-session context-snapshot chain to the right-dock
``Snapshots`` tab so users can inspect what the LLM actually saw on
each turn.
"""
from __future__ import annotations

import json


async def handle_list_snapshots(ws, cmd: dict):
    session_id = cmd.get("session_id")
    snapshots: list[dict] = []
    err: str | None = None
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.context.snapshot import (
            list_snapshots,
            snapshot_state_counts,
        )
        store = default_db()
        if session_id:
            snaps = list_snapshots(store, session_id, limit=50)
            for s in snaps:
                snapshots.append({
                    "id": s.id,
                    "parent_id": s.parent_id,
                    "created_at": s.created_at,
                    "head_node_id": s.head_node_id,
                    "total_tokens": s.total_tokens,
                    "rules_version": s.rules_version,
                    "summary": s.summary,
                    "item_count": len(s.items),
                    "state_counts": snapshot_state_counts(s),
                })
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "snapshots_list",
        "data": {
            "session_id": session_id,
            "snapshots": snapshots,
            "error": err,
        },
    }, default=str))


async def handle_get_snapshot_detail(ws, cmd: dict):
    snap_id = cmd.get("snap_id")
    payload: dict = {"id": snap_id, "error": None, "items": []}
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.context.snapshot import load_snapshot
        store = default_db()
        if not snap_id:
            payload["error"] = "snap_id required"
        else:
            snap = load_snapshot(store, snap_id)
            if snap is None:
                payload["error"] = f"snapshot {snap_id!r} not found"
            else:
                payload.update({
                    "id": snap.id,
                    "session_id": snap.session_id,
                    "parent_id": snap.parent_id,
                    "created_at": snap.created_at,
                    "head_node_id": snap.head_node_id,
                    "rules_version": snap.rules_version,
                    "total_tokens": snap.total_tokens,
                    "summary": snap.summary,
                    "items": [
                        {
                            "source_node_id": i.source_node_id,
                            "role": i.role,
                            "state": i.state,
                            "rendered": i.rendered,
                            "tokens": i.tokens,
                            "reason": i.reason,
                            "locked": i.locked,
                            "is_anchor": i.is_anchor,
                            "merged_into": i.merged_into,
                        }
                        for i in snap.items
                    ],
                })
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "snapshot_detail",
        "data": payload,
    }, default=str))


ACTIONS = {
    "list_snapshots": handle_list_snapshots,
    "get_snapshot_detail": handle_get_snapshot_detail,
}
