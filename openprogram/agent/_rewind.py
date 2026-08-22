"""Transactional multi-turn rewind to a user-message boundary."""
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
        # ROOT is a synthetic user node (empty output), not a real turn —
        # never a rewind target. Offering it let a user rewind "to the
        # beginning", which marks ROOT itself rewound and leaves head
        # pointing at a dead node (rewind_to's new_head loop finds no
        # earlier seq).
        if meta.get("display") == "root":
            continue
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


def rewind_to(
    session_id: str,
    target_msg_id: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Rewind to the state before ``target_msg_id`` was sent.

    ``target_msg_id`` is a **user** node ID. We revert all assistant
    turns from the latest back to (and including) the one that
    answered this user message, then return the user message text
    so the frontend can prefill the composer.
    """
    try:
        from openprogram.store.session.session_store import default_store
        from openprogram.store.snapshot.checkpoint import CheckpointStore
    except Exception as e:
        return _err(session_id, target_msg_id, f"import failed: {e}")

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return _err(session_id, target_msg_id, f"unknown session {session_id!r}")

    _git, idx = pair

    target_node = idx.nodes_by_id.get(target_msg_id)
    if target_node is None:
        return _err(session_id, target_msg_id, f"node {target_msg_id!r} not found")

    user_text = target_node.output or ""
    if isinstance(user_text, dict):
        user_text = str(user_text)

    from openprogram.store.session.session_store import _node_conv_predecessor

    # Walk the ACTIVE chain from head back to the target. The old code
    # marked every node with seq >= target's — but seq is global, so a
    # sibling branch appended after the target (a fork, a /task spawn)
    # got rewound along with the branch the user was actually on.
    chain: list = []
    cur = idx.nodes_by_id.get(idx.head_id) if idx.head_id else None
    while cur is not None:
        chain.append(cur)
        if cur.id == target_msg_id:
            break
        pred = _node_conv_predecessor(cur)
        cur = idx.nodes_by_id.get(pred) if pred else None
    if not chain or chain[-1].id != target_msg_id:
        return _err(session_id, target_msg_id,
                    f"node {target_msg_id!r} is not on the active branch")

    new_head: str | None = _node_conv_predecessor(target_node) or None
    source_head = idx.head_id
    turn_ids = [node.id for node in chain if node.role == "llm"]
    journal = CheckpointStore(store._session_dir(session_id))
    result = journal.apply_rewind_operation(
        turn_ids,
        expected_head_id=source_head,
        target_head_id=new_head,
        get_head=lambda: (store.get_session(session_id) or {}).get("head_id"),
        compare_and_set_head=lambda expected, target: store.compare_and_set_head(
            session_id, expected, target,
        ),
        idempotency_key=idempotency_key,
    )
    committed = result.get("status") == "committed"
    if committed:
        try:
            store.commit_turn(session_id, "rewind")
        except Exception:
            pass
    error = result.get("error")
    return {
        "session_id": session_id,
        "target_msg_id": target_msg_id,
        "user_text": user_text,
        "source_head_id": source_head,
        "turns_reverted": len(turn_ids) if committed else 0,
        "nodes_rewound": len(chain) if committed else 0,
        "total_restored_paths": result.get("restored_paths", []),
        "new_head_id": result.get("new_head_id"),
        "head_changed": bool(result.get("head_changed")),
        "errors": [error] if error else [],
        **result,
    }


def _err(session_id: str, target: str, msg: str) -> dict[str, Any]:
    return {
        "status": "error",
        "session_id": session_id,
        "target_msg_id": target,
        "user_text": "",
        "turns_reverted": 0,
        "nodes_rewound": 0,
        "total_restored_paths": [],
        "new_head_id": None,
        "head_changed": False,
        "error": msg,
        "errors": [msg],
    }
