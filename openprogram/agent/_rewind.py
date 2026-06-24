"""Multi-turn rewind — roll back code and conversation to a chosen point.

Inspired by Claude Code's ``/rewind``: list recent turns, user picks
one, everything after that point gets reverted (files restored from
checkpoints, conversation history truncated).

Unlike single-turn ``revert_turn`` (which only restores files for one
turn), rewind walks backwards from the latest turn to the chosen one,
reverting each in sequence.
"""
from __future__ import annotations

from typing import Any, Optional


def list_rewind_points(session_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent assistant turns as potential rewind targets.

    Each entry::

        {
            "msg_id": str,          # assistant node id (the revert key)
            "seq": int,             # sequence number
            "summary": str,         # first ~80 chars of assistant output
            "created_at": float,    # timestamp
            "files_affected": [...],# files this turn modified (from checkpoint)
            "reverted": bool,       # already reverted?
        }

    Ordered newest-first, up to ``limit`` entries.
    """
    try:
        from openprogram.store.session.session_store import default_store
        from openprogram.store.snapshot.checkpoint import CheckpointStore
    except Exception:
        return []

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return []

    git, idx = pair
    session_dir = git.path if hasattr(git, "path") else store._session_dir(session_id)
    checkpoint = CheckpointStore(session_dir)

    points: list[dict[str, Any]] = []
    for node in reversed(idx.all_nodes()):
        if node.role != "llm":
            continue
        if len(points) >= limit:
            break

        output = node.output or ""
        if isinstance(output, dict):
            output = str(output)
        summary = output[:80].replace("\n", " ").strip()
        if len(output) > 80:
            summary += "..."

        turn_id = node.id
        try:
            files = checkpoint.list_backed_paths(turn_id)
        except Exception:
            files = []

        meta = node.metadata or {}
        points.append({
            "msg_id": node.id,
            "seq": node.seq,
            "summary": summary,
            "created_at": getattr(node, "created_at", 0) or 0,
            "files_affected": files,
            "reverted": bool(meta.get("reverted")),
        })

    return points


def rewind_to(session_id: str, target_msg_id: str) -> dict[str, Any]:
    """Rewind to the state before ``target_msg_id`` was produced.

    Reverts all turns from the latest back to (and including)
    ``target_msg_id``. Returns::

        {
            "session_id": str,
            "target_msg_id": str,
            "turns_reverted": int,
            "total_restored_paths": [...],
            "errors": [...],
        }
    """
    from openprogram.agent._revert import revert_turn

    points = list_rewind_points(session_id, limit=100)
    if not points:
        return {
            "session_id": session_id,
            "target_msg_id": target_msg_id,
            "turns_reverted": 0,
            "total_restored_paths": [],
            "errors": ["no rewind points found"],
        }

    target_found = False
    to_revert: list[str] = []
    for p in points:
        if p.get("reverted"):
            continue
        to_revert.append(p["msg_id"])
        if p["msg_id"] == target_msg_id:
            target_found = True
            break

    if not target_found:
        return {
            "session_id": session_id,
            "target_msg_id": target_msg_id,
            "turns_reverted": 0,
            "total_restored_paths": [],
            "errors": [f"target {target_msg_id!r} not found in recent turns"],
        }

    all_restored: list[str] = []
    errors: list[str] = []

    for msg_id in to_revert:
        result = revert_turn(session_id, msg_id)
        if result.get("error"):
            errors.append(f"{msg_id}: {result['error']}")
        all_restored.extend(result.get("restored_paths", []))

    return {
        "session_id": session_id,
        "target_msg_id": target_msg_id,
        "turns_reverted": len(to_revert),
        "total_restored_paths": list(set(all_restored)),
        "errors": errors,
    }
