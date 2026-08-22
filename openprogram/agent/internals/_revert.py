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
    owned_turn_ids: list[str] = []
    ownership = {"status": "ready", "blockers": [], "linked": []}
    from openprogram.agent.history_ownership import owned_change_set_closure

    ownership = owned_change_set_closure(session_id, [assistant_msg_id])
    if ownership["status"] != "ready":
        return {
            **base,
            "status": "blocked",
            "error": "owned_actor_running",
            "blockers": ownership["blockers"],
            "linked_impacts": ownership["linked"],
        }
    owned_turn_ids = ownership["owned_turn_ids"]
    if owned_turn_ids:
        plan = journal.plan_rewind_operation(
            owned_turn_ids + [assistant_msg_id], direction=direction,
        )
        if plan.get("status") != "ready":
            result = {
                **plan,
                "transaction_id": None,
                "restored_paths": [],
                "new_head_id": None,
                "head_changed": False,
            }
        else:
            sentinel = f"history-closure:{index.head_id or 'ROOT'}"
            result = journal.apply_rewind_operation(
                owned_turn_ids + [assistant_msg_id],
                expected_head_id=sentinel,
                target_head_id=sentinel,
                get_head=lambda: sentinel,
                compare_and_set_head=(
                    lambda expected, target: expected == target == sentinel
                ),
                idempotency_key=(
                    f"turn-closure:{direction}:"
                    f"{idempotency_key or assistant_msg_id}"
                ),
                target_msg_id=f"{direction}:{assistant_msg_id}",
                custom_actions=plan["actions"],
            )
            result["head_changed"] = False
            result["new_head_id"] = None
    else:
        result = journal.apply_history_operation(
            assistant_msg_id,
            direction,
            idempotency_key=idempotency_key,
        )
    result["owned_turn_ids"] = owned_turn_ids
    result["linked_impacts"] = ownership["linked"]
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
