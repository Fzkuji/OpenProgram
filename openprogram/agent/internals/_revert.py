"""Safe single-turn Undo/Revert and Redo/Reapply."""
from __future__ import annotations

import json
import time
from typing import Any


def _result_base(session_id: str, assistant_msg_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "assistant_msg_id": assistant_msg_id,
        "restored_paths": [],
        "metadata_stamped": False,
    }


def _persist_status(git, node, *, reverted: bool, result: dict) -> bool:
    try:
        node.metadata = {
            **(node.metadata or {}),
            "reverted": reverted,
            "reverted_at": time.time() if reverted else None,
            "reapplied_at": None if reverted else time.time(),
            "reverted_paths": list(result.get("restored_paths") or []),
            "history_transaction_id": result.get("transaction_id"),
        }
        role = (node.role or "x")[0]
        path = git.path / "history" / f"{node.seq:04d}-{role}-{node.id}.json"
        if path.exists():
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(node.to_dict(), ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(path)
        return True
    except Exception:
        return False


def _apply(
    session_id: str,
    assistant_msg_id: str,
    direction: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    base = _result_base(session_id or "", assistant_msg_id or "")
    if not session_id or not assistant_msg_id:
        return {**base, "status": "error", "error": (
            "session_id and assistant_msg_id are required"
        )}
    try:
        from openprogram.store.session.session_store import default_store
        from openprogram.store.snapshot.checkpoint import CheckpointStore
    except Exception as exc:
        return {**base, "status": "error", "error": (
            f"import failed: {type(exc).__name__}: {exc}"
        )}

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return {**base, "status": "error", "error": f"unknown session {session_id!r}"}
    git, index = pair
    node = index.nodes_by_id.get(assistant_msg_id)
    if node is None:
        return {**base, "status": "error", "error": (
            f"unknown assistant turn {assistant_msg_id!r}"
        )}
    journal = CheckpointStore(store._session_dir(session_id))
    result = journal.apply_history_operation(
        assistant_msg_id,
        direction,
        idempotency_key=idempotency_key,
    )
    payload = {**base, **result}
    if result.get("status") != "committed":
        return payload
    payload["metadata_stamped"] = _persist_status(
        git, node, reverted=direction == "revert", result=result,
    )
    return payload


def revert_turn(
    session_id: str,
    assistant_msg_id: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return _apply(session_id, assistant_msg_id, "revert", idempotency_key)


def reapply_turn(
    session_id: str,
    assistant_msg_id: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return _apply(session_id, assistant_msg_id, "reapply", idempotency_key)
