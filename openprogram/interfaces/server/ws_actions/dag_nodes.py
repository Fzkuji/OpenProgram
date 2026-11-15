"""DAG node deletion WS actions — delete selected nodes, undo a delete.

Wire format::

    in:  {"action": "delete_nodes", "session_id": "...", "node_ids": [...]}
    out: {"type": "nodes_deleted",
          "data": {"session_id", "ok", "op_id", "deleted", "error"}}

    in:  {"action": "restore_deleted_nodes", "session_id": "...", "op_id": "..."}
    out: {"type": "nodes_restored",
          "data": {"session_id", "ok", "op_id", "error"}}

The store does the splice and keeps the journal
(``session_store/node_deletion.py``); this layer only refuses while a
turn runs and re-syncs the server's head/message cache. The transcript
publisher tells every open view its window went stale, so transcript and
graph both redraw.
"""
from __future__ import annotations

import json
import uuid

from openprogram.state.store.session.conversation_work import run_for_conversation


def _after_graph_change(session_id: str, head_after) -> None:
    from openprogram.interfaces.server import server as _s

    # Always through _set_active_head, even when HEAD stayed: a splice
    # rewrites the active branch itself, and only that call re-reads
    # conv["messages"] (invalidating alone leaves the deleted rows in it).
    _s._set_active_head(session_id, head_after)
    _s.refresh_context_stats(session_id)


def _apply(session_id: str, op) -> dict:
    """Run ``op`` and refresh what the graph change touched."""
    from openprogram.runtime.context.session_db import default_db

    journal = op(session_id)
    head = (default_db().get_session(session_id) or {}).get("head_id")
    _after_graph_change(session_id, head)
    return journal


async def _run(ws, cmd: dict, reply_type: str, op, node_ids=None) -> None:
    """Run a node operation. ``node_ids`` names the nodes it removes: only
    the branches whose line holds one of them have to be free (each held
    for the operation), so a turn on any other branch keeps running.
    Without ids (a restore) every branch has to be free."""
    from openprogram.interfaces.server import server as _s
    from openprogram.state.store.session.session_store.node_deletion import NodeDeletionError

    session_id = cmd.get("session_id")
    data = {"session_id": session_id, "ok": False, "error": None}
    owners: list[str] = []
    refusal = None
    # Every step below takes the conversation's lock: each runs in the
    # conversation's lane, off the event loop.
    if session_id and node_ids is not None:
        touched = await run_for_conversation(session_id, _s.branches_through, session_id, node_ids)
        refusal, owners = await run_for_conversation(
            session_id, _s.reserve_branches_operation, session_id, touched, "branch",
            f"delete-{uuid.uuid4().hex}")
    if not session_id:
        data["error"] = "session_id required"
    elif refusal is not None:
        # The turn on that branch may be writing under the nodes we'd remove.
        data.update(error=refusal["error"], code=refusal.get("code"),
                    blocking=refusal.get("blocking"))
    elif node_ids is None and await run_for_conversation(session_id, _s._is_run_active, session_id):
        data["error"] = _s.RUN_ACTIVE_ERROR
    else:
        try:
            journal = await run_for_conversation(session_id, _apply, session_id, op)
            data.update(ok=True, op_id=journal.get("op_id"),
                        deleted=len(journal.get("deleted") or []))
        except NodeDeletionError as e:
            data["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            data["error"] = f"{type(e).__name__}: {e}"
    if owners:
        await run_for_conversation(session_id, _s.release_branches_operation, session_id, owners)
    await ws.send_text(json.dumps({"type": reply_type, "data": data}, default=str))


async def handle_delete_nodes(ws, cmd: dict) -> None:
    from openprogram.runtime.context.session_db import default_db

    ids = [str(x) for x in (cmd.get("node_ids") or []) if x]
    await _run(ws, cmd, "nodes_deleted",
               lambda sid: default_db().delete_nodes(sid, ids), node_ids=ids)


async def handle_restore_deleted_nodes(ws, cmd: dict) -> None:
    from openprogram.runtime.context.session_db import default_db

    op_id = str(cmd.get("op_id") or "")
    await _run(ws, cmd, "nodes_restored",
               lambda sid: default_db().restore_deleted_nodes(sid, op_id))


ACTIONS = {
    "delete_nodes": handle_delete_nodes,
    "restore_deleted_nodes": handle_restore_deleted_nodes,
}
