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


def _persist_status(session_log, node, *, reverted: bool, result: dict) -> bool:
    try:
        node.metadata = {
            **(node.metadata or {}),
            "reverted": reverted,
            "reverted_at": time.time() if reverted else None,
            "reapplied_at": None if reverted else time.time(),
            "reverted_paths": list(result.get("restored_paths") or []),
            "history_transaction_id": result.get("transaction_id"),
        }
        session_log.write_history(node.seq, node.role, node.id, node.to_dict())
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
        from openprogram.state.store.session.session_store import default_store
        from openprogram.state.store.snapshot.checkpoint import CheckpointStore
    except Exception as exc:
        return {**base, "status": "error", "error": (
            f"import failed: {type(exc).__name__}: {exc}"
        )}

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return {**base, "status": "error", "error": f"unknown session {session_id!r}"}
    session_log, index = pair
    node = index.nodes_by_id.get(assistant_msg_id)
    if node is None:
        return {**base, "status": "error", "error": (
            f"unknown assistant turn {assistant_msg_id!r}"
        )}
    journal = CheckpointStore(store._session_dir(session_id))
    owned_turn_ids: list[str] = []
    ownership = {"status": "ready", "blockers": [], "linked": []}
    from openprogram.runtime.conversation.history_ownership import owned_change_set_closure

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
    # One critical section from the plan to the outcome: a second request
    # with the same key waits for the first and then replays its intent,
    # instead of planning against the files the first one restored or
    # reading its unfinished intent as one that needs recovery. The
    # recovery scope keeps the head callbacks' opens from replaying this
    # very intent: a cache miss inside the journal's workspace lock would
    # otherwise take that lock again and hang.
    with store._conversation_write_lock(session_id), store._rewind_recovery_scope(session_id):
        if owned_turn_ids:
            result = _apply_owned_closure(
                store, journal, session_id, assistant_msg_id, direction,
                idempotency_key, owned_turn_ids,
            )
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
        session_log, node, reverted=direction == "revert", result=result,
    )
    return payload


def _apply_owned_closure(
    store, journal, session_id, assistant_msg_id, direction, idempotency_key, owned_turn_ids,
) -> dict[str, Any]:
    """Plan and apply a turn's file closure; the caller holds the write lock."""
    key = f"turn-closure:{direction}:{idempotency_key or assistant_msg_id}"
    target_msg_id = f"{direction}:{assistant_msg_id}"
    turn_ids = owned_turn_ids + [assistant_msg_id]
    head_id = (store.get_session(session_id) or {}).get("head_id")
    existing = journal.read_rewind_intent(key)
    if existing is None:
        plan = journal.plan_rewind_operation(turn_ids, direction=direction)
        if plan.get("status") != "ready":
            return {
                **plan,
                "transaction_id": None,
                "restored_paths": [],
                "new_head_id": None,
                "head_changed": False,
            }
        actions = plan["actions"]
    else:
        # The journal replays the outcome recorded under this key.
        head_id = existing.get("expected_head_id")
        actions = existing.get("actions") or []
    result = journal.apply_rewind_operation(
        turn_ids,
        expected_head_id=head_id,
        target_head_id=head_id,
        get_head=lambda: (
            (store.get_session(session_id) or {}).get("head_id")
        ),
        compare_and_set_head=lambda expected, target, rollback=False: (
            expected == target == head_id
            and store.compare_and_set_head(session_id, expected, target)
        ),
        idempotency_key=key,
        target_msg_id=target_msg_id,
        custom_actions=actions,
    )
    result["head_changed"] = False
    result["new_head_id"] = None
    return result


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
