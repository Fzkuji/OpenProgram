"""Deterministic original-session notifications from durable update results."""
from __future__ import annotations

import json

from .projection import _exists, _optional, _project, _records
from .store import SelfUpdateStore
from .types import is_terminal
from .verification_channel import _digest

MAX_DELIVERIES = 20


def _results(snapshot):
    yield "terminal"
    for key in ("diagnosis", "source_repair_result"):
        value = snapshot[key]
        if value is not None:
            yield f"{key}:{value['status']}"
    submission = (snapshot["iteration"] or {}).get("submission")
    if submission is not None:
        yield f"iteration:{submission['status']}"
    runtime = snapshot["last_verified_runtime"]
    if runtime is not None and runtime["source"] == "owner_repair":
        yield "owner_repair"


def _persist(db, snapshot, kind, node_id):
    from openprogram.context.nodes import Call
    from openprogram.store import SessionNodeWriter

    session_id = snapshot["session_id"]
    with db._session_lock(session_id):
        pair = db._open(session_id)
        if pair is None:
            return False
        _, index = pair
        origin = index.nodes_by_id.get(snapshot["origin_assistant_id"])
        if origin is None or origin.role != "llm":
            return False
        existing = index.nodes_by_id.get(node_id)
        if existing is not None and (
            existing.metadata.get("source") != "self_update_result"
            or existing.metadata.get("result_kind") != kind
            or existing.metadata.get("self_update", {}).get("update_id") != snapshot["update_id"]
            or existing.caller != origin.id
        ):
            raise ValueError("self-update result identity conflicts with session history")
        summary = {k: snapshot[k] for k in (
            "update_id", "attempt", "phase", "candidate_revision", "last_verified_runtime",
            "verifier_verdict", "diagnosis", "source_repair_result", "iteration",
        )}
        node = existing or Call(
            id=node_id, role="code", name="self_update_result",
            created_at=snapshot["updated_at"], caller=origin.id, predecessor=origin.id,
            output=json.dumps(summary, ensure_ascii=False, allow_nan=False),
            metadata={"source": "self_update_result", "result_kind": kind,
                      "status": "completed", "self_update": snapshot},
        )
        SessionNodeWriter(db, session_id, advance_head=False).append(node, create_if_missing=False)
        return db._open(session_id) is not None


def deliver_pending() -> int:
    """Reconcile at most twenty notifications, without a model or client."""
    from openprogram.agent.session_db import default_db

    store = SelfUpdateStore()
    if not _exists(store):
        return 0
    delivered = 0
    sessions = set()
    with store._locked(read_only=True):
        for record in _records(store):
            if not is_terminal(record.state.phase):
                continue
            path = store.root / record.request.update_id / "delivery.json"
            receipt = _optional(path)
            binding = dict(schema=1, update_id=record.request.update_id, attempt=record.state.attempt)
            if receipt is None:
                receipt = {**binding, "delivered": {}}
            if (set(receipt) != {*binding, "delivered"}
                    or type(receipt["schema"]) is not int or type(receipt["attempt"]) is not int
                    or any(receipt[k] != v for k, v in binding.items())
                    or not isinstance(receipt["delivered"], dict)):
                raise ValueError("invalid self-update delivery receipt")
            snapshot = _project(store, record)
            for kind in _results(snapshot):
                node_id = f"su_result_{_digest([record.request.update_id, record.state.attempt, kind])[:32]}"
                prior = receipt["delivered"].get(kind)
                if prior is not None:
                    if prior != node_id:
                        raise ValueError("invalid self-update delivery identity")
                    continue
                if _persist(default_db(), snapshot, kind, node_id):
                    receipt["delivered"][kind] = node_id
                    store._write_json(path, receipt)
                    sessions.add(record.request.session_id)
                    delivered += 1
                if delivered >= MAX_DELIVERIES:
                    break
            if delivered >= MAX_DELIVERIES:
                break
    if sessions:
        from openprogram.agent.job.runner import _broadcast_session_reload
        for session_id in sessions:
            _broadcast_session_reload(session_id, reason="self_update_result")
    return delivered
