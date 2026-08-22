"""Conversation-branch and workspace alignment state."""
from __future__ import annotations

import time
import uuid
from typing import Any


def get_workspace_alignment(session_id: str, *, store=None) -> dict[str, Any]:
    if store is None:
        from openprogram.store.session.session_store import default_store

        store = default_store()
    session = store.get_session(session_id) or {}
    value = session.get("workspace_alignment")
    if isinstance(value, dict):
        return dict(value)
    return {
        "status": "aligned",
        "head_id": session.get("head_id"),
        "decision": "implicit_existing",
    }


def mark_conversation_checkout(
    session_id: str,
    source_head_id: str | None,
    target_head_id: str,
    *,
    store=None,
) -> dict[str, Any]:
    if store is None:
        from openprogram.store.session.session_store import default_store

        store = default_store()
    prior = get_workspace_alignment(session_id, store=store)
    workspace_head_id = (
        prior.get("source_head_id")
        if prior.get("status") == "mismatch"
        else source_head_id
    )
    aligned = target_head_id == workspace_head_id
    value = {
        "status": "aligned" if aligned else "mismatch",
        "reason": "conversation_checkout",
        # Consecutive conversation checkouts must keep pointing at the
        # branch whose files are still materialized, not the branch that was
        # only selected in the transcript between the two checkouts.
        "source_head_id": workspace_head_id,
        "target_head_id": target_head_id,
        "head_id": target_head_id,
        "decision": "return_to_workspace_branch" if aligned else None,
        "updated_at": time.time(),
    }
    store.update_session(session_id, workspace_alignment=value)
    return value


def adopt_current_workspace(
    session_id: str,
    *,
    store=None,
    decision: str = "keep_current_files",
) -> dict[str, Any]:
    if store is None:
        from openprogram.store.session.session_store import default_store

        store = default_store()
    session = store.get_session(session_id) or {}
    prior = get_workspace_alignment(session_id, store=store)
    value = {
        **prior,
        "status": "aligned",
        "head_id": session.get("head_id"),
        "decision": decision,
        "updated_at": time.time(),
    }
    store.update_session(session_id, workspace_alignment=value)
    return value


def _branch_turn_ids(store, session_id: str, head_id: str | None) -> list[str]:
    if not head_id:
        return []
    return [
        message["id"]
        for message in (store.get_branch(session_id, head_id) or [])
        if message.get("role") == "assistant" and message.get("id")
    ]


def _branch_projection(journal, turn_ids: list[str]) -> tuple[dict, list[str]]:
    projection: dict[str, dict] = {}
    unavailable: list[str] = []
    for turn_id in turn_ids:
        for mutation in journal.list_mutations(turn_id):
            path = mutation.get("path") or ""
            before = mutation.get("before") or {}
            after = mutation.get("after") or {}
            if (
                not path
                or mutation.get("recoverability") != "exact"
                or before.get("kind") not in {"regular", "absent"}
                or after.get("kind") not in {"regular", "absent"}
            ):
                unavailable.append(path)
                continue
            before = journal._state_with_blob(turn_id, before)
            after = journal._state_with_blob(turn_id, after)
            try:
                chain = journal._capture_parent_chain(path)
            except OSError:
                unavailable.append(path)
                continue
            before["parent_chain"] = chain
            after["parent_chain"] = chain
            current = projection.get(path)
            if current is None:
                projection[path] = {
                    "initial": before,
                    "current": after,
                    "turn_ids": [turn_id],
                }
                continue
            if not journal._same_recorded_state(current["current"], before):
                unavailable.append(path)
            current["current"] = after
            current["turn_ids"].append(turn_id)
    return projection, sorted(set(filter(None, unavailable)))


def plan_branch_workspace_restore(session_id: str, *, store=None) -> dict[str, Any]:
    if store is None:
        from openprogram.store.session.session_store import default_store

        store = default_store()
    alignment = get_workspace_alignment(session_id, store=store)
    if alignment.get("status") != "mismatch":
        return {"status": "error", "error": "workspace is already aligned"}
    source_head = alignment.get("source_head_id")
    target_head = alignment.get("target_head_id")
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    journal = CheckpointStore(store._session_dir(session_id))
    source, source_unavailable = _branch_projection(
        journal, _branch_turn_ids(store, session_id, source_head),
    )
    target, target_unavailable = _branch_projection(
        journal, _branch_turn_ids(store, session_id, target_head),
    )
    unavailable = sorted(set(source_unavailable + target_unavailable))
    actions = []
    for path in sorted(set(source) | set(target)):
        source_state = (
            source[path]["current"] if path in source else target[path]["initial"]
        )
        target_state = (
            target[path]["current"] if path in target else source[path]["initial"]
        )
        if journal._same_recorded_state(source_state, target_state):
            continue
        actions.append({
            "path": path,
            "expected_current": source_state,
            "target": target_state,
            "rollback": source_state,
            "turn_ids": (
                source.get(path, {}).get("turn_ids", [])
                + target.get(path, {}).get("turn_ids", [])
            ),
            "state": "pending",
            "error": None,
        })
    if unavailable:
        return {
            "status": "unavailable", "actions": actions,
            "unavailable": unavailable,
            "error": "branch projection is incomplete",
        }
    validated = journal._validate_custom_history_actions(actions)
    return {
        **validated,
        "source_head_id": source_head,
        "target_head_id": target_head,
    }


def restore_branch_workspace(
    session_id: str,
    *,
    store=None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if store is None:
        from openprogram.store.session.session_store import default_store

        store = default_store()
    plan = plan_branch_workspace_restore(session_id, store=store)
    if plan.get("status") != "ready":
        return plan
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    journal = CheckpointStore(store._session_dir(session_id))
    head_id = (store.get_session(session_id) or {}).get("head_id")
    result = journal.apply_rewind_operation(
        [f"branch-switch:{plan.get('source_head_id')}:{plan.get('target_head_id')}"],
        expected_head_id=head_id,
        target_head_id=head_id,
        get_head=lambda: (store.get_session(session_id) or {}).get("head_id"),
        compare_and_set_head=lambda expected, target: expected == target == head_id,
        idempotency_key=(idempotency_key or f"branch-switch:{uuid.uuid4().hex}"),
        target_msg_id=f"branch-switch:{plan.get('target_head_id')}",
        custom_actions=plan["actions"],
    )
    result["head_changed"] = False
    result["new_head_id"] = None
    if result.get("status") == "committed":
        result["workspace_alignment"] = adopt_current_workspace(
            session_id, store=store, decision="restore_branch_code",
        )
    return result
