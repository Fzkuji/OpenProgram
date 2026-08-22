"""Plan and apply transactional multi-turn rewind operations."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any


def _active_chain(index) -> list:
    from openprogram.store.session.session_store import _node_conv_predecessor

    chain = []
    seen: set[str] = set()
    node = index.nodes_by_id.get(index.head_id) if index.head_id else None
    while node is not None and node.id not in seen:
        seen.add(node.id)
        chain.append(node)
        predecessor = _node_conv_predecessor(node)
        node = index.nodes_by_id.get(predecessor) if predecessor else None
    return chain


def _branch_id(session_id: str, head_id: str | None) -> str:
    digest = hashlib.sha256(f"{session_id}\0{head_id or 'ROOT'}".encode()).hexdigest()
    return f"branch_{digest[:16]}"


def _head(store, session_id: str) -> str | None:
    return (store.get_session(session_id) or {}).get("head_id")


def _cas_for_intent(store, session_id: str, intent: dict, expected, target) -> bool:
    source_branch_id = intent.get("source_branch_id")
    target_branch_id = intent.get("target_branch_id")
    forward = (
        expected == intent.get("expected_head_id")
        and target == intent.get("target_head_id")
    )
    update = None
    if source_branch_id and target_branch_id:
        update = {
            "source_branch_id": source_branch_id,
            "target_branch_id": target_branch_id,
            "active_branch_id": target_branch_id if forward else source_branch_id,
            "target_status": "active" if forward else "aborted",
            "preserve_target": not forward,
        }
    return store.compare_and_set_head(
        session_id, expected, target, branch_update=update,
    )


def recover_session_rewinds(session_id: str, *, store=None) -> list[dict]:
    from openprogram.store.session.session_store import default_store
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    store = store or default_store()
    if store._open(session_id) is None:
        return []
    journal = CheckpointStore(store._session_dir(session_id))
    return journal.recover_rewind_intents(
        get_head=lambda: _head(store, session_id),
        compare_and_set_head=lambda intent, expected, target: _cas_for_intent(
            store, session_id, intent, expected, target,
        ),
    )


def recover_all_rewinds() -> int:
    from openprogram.store.session.session_store import default_store

    store = default_store()
    recovered = 0
    for session in store.list_sessions(limit=100_000, include_archived=True):
        session_id = session.get("id")
        if session_id:
            recovered += len(recover_session_rewinds(session_id, store=store))
    return recovered


def list_rewind_points(session_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return user turns on the active predecessor chain, newest first."""
    try:
        from openprogram.store.session.session_store import default_store
        from openprogram.store.snapshot.checkpoint import CheckpointStore
    except Exception:
        return []

    store = default_store()
    recover_session_rewinds(session_id, store=store)
    pair = store._open(session_id)
    if pair is None:
        return []
    _git, index = pair
    journal = CheckpointStore(store._session_dir(session_id))
    chain = _active_chain(index)
    points: list[dict[str, Any]] = []
    for position, node in enumerate(chain):
        if node.role != "user" or (node.metadata or {}).get("display") == "root":
            continue
        if len(points) >= limit:
            break
        output = node.output or ""
        if isinstance(output, dict):
            output = str(output)
        summary = output[:80].replace("\n", " ").strip()
        if len(output) > 80:
            summary += "..."
        files: list[str] = []
        for candidate in reversed(chain[:position]):
            if candidate.role == "user":
                break
            if candidate.role == "llm":
                files = journal.list_backed_paths(candidate.id)
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


def plan_rewind(
    session_id: str,
    target_msg_id: str,
    *,
    mode: str = "code_and_conversation",
) -> dict[str, Any]:
    """Return a read-only rewind plan. This function never changes HEAD/files."""
    if mode != "code_and_conversation":
        return _err(session_id, target_msg_id, f"unsupported rewind mode {mode!r}")
    try:
        from openprogram.store.session.session_store import (
            _node_conv_predecessor,
            default_store,
        )
        from openprogram.store.snapshot.checkpoint import CheckpointStore
    except Exception as exc:
        return _err(session_id, target_msg_id, f"import failed: {exc}")

    store = default_store()
    recover_session_rewinds(session_id, store=store)
    pair = store._open(session_id)
    if pair is None:
        return _err(session_id, target_msg_id, f"unknown session {session_id!r}")
    _git, index = pair
    target = index.nodes_by_id.get(target_msg_id)
    if target is None:
        return _err(session_id, target_msg_id, f"node {target_msg_id!r} not found")
    if target.role != "user" or (target.metadata or {}).get("display") == "root":
        return _err(session_id, target_msg_id, "target must be a non-root user turn")
    chain = _active_chain(index)
    target_position = next(
        (position for position, node in enumerate(chain) if node.id == target_msg_id),
        None,
    )
    if target_position is None:
        return _err(
            session_id, target_msg_id,
            f"node {target_msg_id!r} is not on the active branch",
        )
    suffix = chain[:target_position + 1]
    turn_ids = [node.id for node in suffix if node.role == "llm"]
    source_head = index.head_id
    target_head = _node_conv_predecessor(target)
    if target_head is None:
        return _err(
            session_id, target_msg_id,
            "rewind target has no persistent predecessor",
        )
    journal = CheckpointStore(store._session_dir(session_id))
    file_plan = journal.plan_rewind_operation(turn_ids)
    plan_hash = journal.rewind_plan_hash(
        turn_ids, source_head, target_head, file_plan.get("actions", []),
    )
    refs = index.meta.get("branch_refs") or {}
    source_branch_id = index.meta.get("active_branch_id")
    if not source_branch_id or (refs.get(source_branch_id) or {}).get(
        "head_id"
    ) != source_head:
        source_branch_id = _branch_id(session_id, source_head)
    user_text = target.output or ""
    if isinstance(user_text, dict):
        user_text = str(user_text)
    error = file_plan.get("error")
    return {
        "status": file_plan.get("status", "error"),
        "phase": "plan",
        "mode": mode,
        "session_id": session_id,
        "target_msg_id": target_msg_id,
        "source_head_id": source_head,
        "target_head_id": target_head,
        "source_branch_id": source_branch_id,
        "turn_ids": turn_ids,
        "turns_reverted": len(turn_ids),
        "nodes_rewound": len(suffix),
        "user_text": user_text,
        "plan_hash": plan_hash,
        "idempotency_key": uuid.uuid4().hex,
        "files": [
            {
                "path": action["path"],
                "turn_ids": action.get("turn_ids", []),
                "current": action["expected_current"].get("digest")
                    or action["expected_current"].get("kind"),
                "target": action["target"].get("digest")
                    or action["target"].get("kind"),
            }
            for action in file_plan.get("actions", [])
        ],
        "conflicts": file_plan.get("conflicts", []),
        "unavailable": file_plan.get("unavailable", []),
        "error": error,
        "errors": [error] if error else [],
        "new_head_id": None,
        "head_changed": False,
    }


def _operation_response(
    session_id: str,
    target_msg_id: str,
    result: dict,
    *,
    nodes_rewound: int,
) -> dict[str, Any]:
    committed = result.get("status") == "committed"
    error = result.get("error")
    turn_ids = result.get("turn_ids") or []
    return {
        "session_id": session_id,
        "target_msg_id": target_msg_id,
        "user_text": result.get("user_text", ""),
        "source_head_id": result.get("source_head_id"),
        "turns_reverted": len(turn_ids) if committed else 0,
        "nodes_rewound": nodes_rewound if committed else 0,
        "total_restored_paths": result.get("restored_paths", []),
        "errors": [error] if error else [],
        **result,
    }


def rewind_to(
    session_id: str,
    target_msg_id: str,
    *,
    idempotency_key: str | None = None,
    expected_plan_hash: str | None = None,
    mode: str = "code_and_conversation",
) -> dict[str, Any]:
    """Apply a confirmed plan; files commit before the target branch activates."""
    try:
        from openprogram.store.session.session_store import default_store
        from openprogram.store.snapshot.checkpoint import CheckpointStore
    except Exception as exc:
        return _err(session_id, target_msg_id, f"import failed: {exc}")

    store = default_store()
    if store._open(session_id) is None:
        return _err(session_id, target_msg_id, f"unknown session {session_id!r}")
    journal = CheckpointStore(store._session_dir(session_id))
    if idempotency_key:
        existing = journal.read_rewind_intent(idempotency_key)
        if existing is not None:
            result = journal.apply_rewind_operation(
                existing.get("turn_ids") or [],
                expected_head_id=existing.get("expected_head_id"),
                target_head_id=existing.get("target_head_id"),
                get_head=lambda: _head(store, session_id),
                compare_and_set_head=lambda expected, target: _cas_for_intent(
                    store, session_id, existing, expected, target,
                ),
                idempotency_key=idempotency_key,
                target_msg_id=target_msg_id,
                user_text=existing.get("user_text", ""),
                source_branch_id=existing.get("source_branch_id"),
                target_branch_id=existing.get("target_branch_id"),
                expected_plan_hash=expected_plan_hash,
            )
            return _operation_response(
                session_id, target_msg_id, result,
                nodes_rewound=len(existing.get("turn_ids") or []) * 2,
            )

    plan = plan_rewind(session_id, target_msg_id, mode=mode)
    if plan.get("status") != "ready":
        return plan
    if expected_plan_hash and expected_plan_hash != plan.get("plan_hash"):
        return {
            **plan,
            "status": "aborted",
            "phase": "apply",
            "error": "stale_plan",
            "errors": ["stale_plan"],
        }
    key = idempotency_key or uuid.uuid4().hex
    target_branch_id = f"branch_restore_{uuid.uuid4().hex[:16]}"
    intent_context = {
        "expected_head_id": plan["source_head_id"],
        "target_head_id": plan["target_head_id"],
        "source_branch_id": plan["source_branch_id"],
        "target_branch_id": target_branch_id,
    }
    result = journal.apply_rewind_operation(
        plan["turn_ids"],
        expected_head_id=plan["source_head_id"],
        target_head_id=plan["target_head_id"],
        get_head=lambda: _head(store, session_id),
        compare_and_set_head=lambda expected, target: _cas_for_intent(
            store, session_id, intent_context, expected, target,
        ),
        idempotency_key=key,
        target_msg_id=target_msg_id,
        user_text=plan["user_text"],
        source_branch_id=plan["source_branch_id"],
        target_branch_id=target_branch_id,
        expected_plan_hash=plan["plan_hash"],
    )
    if result.get("status") == "committed" and not result.get("replayed"):
        try:
            store.commit_turn(session_id, "rewind")
        except Exception:
            pass
    return _operation_response(
        session_id, target_msg_id, result,
        nodes_rewound=plan["nodes_rewound"],
    )


def _err(session_id: str, target: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "phase": "plan",
        "session_id": session_id,
        "target_msg_id": target,
        "user_text": "",
        "turns_reverted": 0,
        "nodes_rewound": 0,
        "total_restored_paths": [],
        "new_head_id": None,
        "head_changed": False,
        "error": message,
        "errors": [message],
    }
