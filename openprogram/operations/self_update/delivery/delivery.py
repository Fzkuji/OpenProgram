"""Deterministic original-session notifications from durable update results."""
from __future__ import annotations

import json
import logging
import os
import time

from ..control.projection import _exists
from ..control.projection import _optional
from ..control.projection import _project
from ..control.projection import _records
from ..store import SelfUpdateStore
from ..types import is_terminal
from ..verification.verification_channel import _digest

MAX_DELIVERIES = 20
# Records are checked again this often even when no file changed: a result
# can also depend on the clock and on files kept outside the store.
RECHECK_SECONDS = 300.0
_log = logging.getLogger(__name__)
_clock = time.monotonic
# Per store root: the files the store had, the updates with nothing left to
# deliver under those files, and when that was last checked in full.
_settled: dict[str, tuple[tuple, set[str], float]] = {}


def _fingerprint(root) -> tuple:
    """Name, size and write time of every JSON file of every update.

    Each result an update can deliver is written as a JSON file in the
    folder of that update or of another one in its chain, so an update
    whose results were all delivered has nothing new while these stay the
    same. Checking them costs a directory listing per update; projecting
    an update validates its receipts and schemas again.
    """
    found = []
    with os.scandir(root) as updates:
        for update in updates:
            if not update.name.startswith("su_"):
                continue
            try:
                with os.scandir(update.path) as files:
                    for entry in files:
                        if entry.name.endswith(".json"):
                            status = entry.stat(follow_symlinks=False)
                            found.append((update.name, entry.name, status.st_size, status.st_mtime_ns))
            except OSError:
                found.append((update.name, "", 0, 0))
    return tuple(sorted(found))


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
    from openprogram.state.store.session.nodes import Call
    from openprogram.state.store import SessionNodeWriter

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
        for key in ("dev_deploy", "versions"):
            if key in snapshot:
                summary[key] = snapshot[key]
        node = existing or Call(
            id=node_id, role="code", name="self_update_result",
            created_at=snapshot["updated_at"], caller=origin.id, predecessor=origin.id,
            output=json.dumps(summary, ensure_ascii=False, allow_nan=False),
            metadata={"source": "self_update_result", "result_kind": kind,
                      "status": "completed", "self_update": snapshot},
        )
        SessionNodeWriter(db, session_id, advance_head=False).append(node, create_if_missing=False)
        return db._open(session_id) is not None


def _deliver(store, record, budget) -> tuple[int, bool]:
    """Deliver what this update still owes; say whether nothing is left."""
    from openprogram.runtime.context.session_db import default_db

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
    delivered = 0
    complete = True
    for kind in _results(snapshot):
        if delivered >= budget:
            complete = False
            break
        node_id = f"su_result_{_digest([record.request.update_id, record.state.attempt, kind])[:32]}"
        prior = receipt["delivered"].get(kind)
        if prior is not None:
            if prior != node_id:
                raise ValueError("invalid self-update delivery identity")
            continue
        if _persist(default_db(), snapshot, kind, node_id):
            receipt["delivered"][kind] = node_id
            store._write_json(path, receipt)
            delivered += 1
        else:
            complete = False
    return delivered, complete


def deliver_pending() -> int:
    """Reconcile at most twenty notifications, without a model or client."""
    store = SelfUpdateStore()
    if not _exists(store):
        return 0
    delivered = 0
    key = str(store.root)
    with store._locked(read_only=True):
        # Taken before the records are read: a file written meanwhile makes
        # the next pass look again.
        files, now = _fingerprint(store.root), _clock()
        known = _settled.get(key)
        if known is None or known[0] != files or now - known[2] >= RECHECK_SECONDS:
            known = (files, set(), now)
        done = known[1]
        if done and done == {entry[0] for entry in files}:
            return 0
        for record in _records(store):
            update_id = record.request.update_id
            if update_id in done or not is_terminal(record.state.phase):
                continue
            if delivered >= MAX_DELIVERIES:
                break
            try:
                count, complete = _deliver(store, record, MAX_DELIVERIES - delivered)
            except ValueError:
                # Fail closed for this update only; the others still reach
                # their sessions.
                _log.warning("self-update result %s not delivered", update_id, exc_info=True)
                continue
            delivered += count
            if complete and not count:
                done.add(update_id)
        _settled[key] = known
    return delivered
