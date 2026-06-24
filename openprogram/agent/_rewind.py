"""Multi-turn rewind — roll back to a chosen user message.

The user clicks ↩ on a user message. We:
1. Restore files to the state before that message was sent (via checkpoints)
2. Return the user message text so the frontend can prefill the input box
3. Mark all rewound nodes so the current branch no longer shows them

The DAG is append-only — rewound nodes stay in the graph as a historical
branch, they just stop being on the active conversation path.
"""
from __future__ import annotations

from typing import Any


def list_rewind_points(session_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent user turns as potential rewind targets.

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
        if node.role != "user":
            continue
        meta = node.metadata or {}
        if meta.get("rewound"):
            continue
        if len(points) >= limit:
            break

        output = node.output or ""
        if isinstance(output, dict):
            output = str(output)
        summary = output[:80].replace("\n", " ").strip()
        if len(output) > 80:
            summary += "..."

        # Check if the next assistant turn has file backups
        files: list[str] = []
        all_nodes = idx.all_nodes()
        node_idx = next((i for i, n in enumerate(all_nodes) if n.id == node.id), -1)
        if node_idx >= 0:
            for j in range(node_idx + 1, len(all_nodes)):
                nj = all_nodes[j]
                if nj.role == "llm":
                    try:
                        files = checkpoint.list_backed_paths(nj.id)
                    except Exception:
                        pass
                    break

        points.append({
            "msg_id": node.id,
            "seq": node.seq,
            "summary": summary,
            "user_text": output,
            "created_at": getattr(node, "created_at", 0) or 0,
            "files_affected": files,
        })

    return points


def rewind_to(session_id: str, target_msg_id: str) -> dict[str, Any]:
    """Rewind to the state before ``target_msg_id`` was sent.

    ``target_msg_id`` is a **user** node ID. We revert all assistant
    turns from the latest back to (and including) the one that
    answered this user message, then return the user message text
    so the frontend can prefill the composer.
    """
    from openprogram.agent._revert import revert_turn

    try:
        from openprogram.store.session.session_store import default_store
    except Exception as e:
        return _err(session_id, target_msg_id, f"import failed: {e}")

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return _err(session_id, target_msg_id, f"unknown session {session_id!r}")

    _, idx = pair
    all_nodes = idx.all_nodes()

    # Find the target user node
    target_node = idx.nodes_by_id.get(target_msg_id)
    if target_node is None:
        return _err(session_id, target_msg_id, f"node {target_msg_id!r} not found")

    # Extract user text for prefilling the composer
    user_text = target_node.output or ""
    if isinstance(user_text, dict):
        user_text = str(user_text)

    target_seq = target_node.seq

    # Collect all assistant (llm) nodes at or after the target's seq
    # These are the turns we need to revert, newest first
    to_revert: list[str] = []
    to_mark: list[str] = []
    for node in reversed(all_nodes):
        if node.seq < target_seq:
            break
        to_mark.append(node.id)
        if node.role == "llm":
            meta = node.metadata or {}
            if not meta.get("reverted"):
                to_revert.append(node.id)

    # Revert file changes for each assistant turn
    all_restored: list[str] = []
    errors: list[str] = []
    for msg_id in to_revert:
        result = revert_turn(session_id, msg_id)
        if result.get("error"):
            errors.append(f"{msg_id}: {result['error']}")
        all_restored.extend(result.get("restored_paths", []))

    # Mark all rewound nodes (user + assistant + code)
    import time
    for node_id in to_mark:
        node = idx.nodes_by_id.get(node_id)
        if node is not None:
            node.metadata = {
                **(node.metadata or {}),
                "rewound": True,
                "rewound_at": time.time(),
            }

    return {
        "session_id": session_id,
        "target_msg_id": target_msg_id,
        "user_text": user_text,
        "turns_reverted": len(to_revert),
        "nodes_rewound": len(to_mark),
        "total_restored_paths": list(set(all_restored)),
        "errors": errors,
    }


def _err(session_id: str, target: str, msg: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "target_msg_id": target,
        "user_text": "",
        "turns_reverted": 0,
        "nodes_rewound": 0,
        "total_restored_paths": [],
        "errors": [msg],
    }
