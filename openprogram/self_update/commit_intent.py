"""Preserve an accepted commit decision across irreversible App finalization."""
from __future__ import annotations

import math
from pathlib import Path
import re
import time

from .types import UpdatePhase, _validate_update_id
from .verification_channel import _read, _digest, _sign, _check_signature, load_grant


def _path(store, record) -> Path:
    return store.root / record.request.update_id / f"commit-{record.state.attempt}.json"


def commit_pending(store, record) -> bool:
    """Admission waits even on an invalid receipt; only the controller validates it."""
    path = _path(store, record)
    return path.exists() or path.is_symlink()


def read_journal(transaction: Path) -> dict:
    value = _read(transaction / "transaction.json")
    keys = {"schema", "phase", "previous_sha256", "active_sha256", "app", "worker", "launchd"}
    if "reopen_update_id" in value:
        keys.add("reopen_update_id")
        _validate_update_id(value["reopen_update_id"])
    if (set(value) != keys
        or type(value["schema"]) is not int or value["schema"] != 1
        or value["phase"] not in {"prepared", "activating", "activated", "rolling_back", "committing", "committed", "rolled_back"}
        or any(not isinstance(value[k], str) or not re.fullmatch(r"[0-9a-f]{64}", value[k])
               for k in ("previous_sha256", "active_sha256"))
        or any(type(value[k]) is not bool for k in ("app", "worker", "launchd"))):
        raise ValueError("invalid commit transaction journal")
    return value


def load_commit(store, record, transaction: Path, installer_sha256: str) -> tuple[dict, dict]:
    """Validate a historical decision without authorizing a new verifier grant."""
    value = _read(_path(store, record))
    directory = store.root / record.request.update_id
    grant = _read(directory / f"verifier-grant-{record.state.attempt}.json")
    receipt = _read(directory / f"verifier-result-{record.state.attempt}.json")
    if _digest(grant) != record.state.detail.get("verifier_grant_sha256"):
        raise ValueError("commit grant differs from the original authorization")
    _check_signature(value, grant["token"])
    _check_signature(receipt, grant["token"])
    expected = dict(schema=1, update_id=record.request.update_id, candidate_sha=record.request.candidate_sha,
                    attempt=record.state.attempt, transaction_dir=str(transaction), installer_sha256=installer_sha256,
                    grant_sha256=_digest(grant), result_sha256=_digest(receipt))
    if (set(value) != {*expected, "transaction_identity", "system_gate", "decided_at", "signature"}
        or any(value[k] != v for k, v in expected.items())
        or record.state.phase not in {UpdatePhase.VERIFYING, UpdatePhase.SUCCEEDED, UpdatePhase.NEEDS_MANUAL_RECOVERY}
        or record.state.detail.get("transaction_dir") != str(transaction)
        or receipt.get("verdict") != "pass"
        or receipt.get("grant_sha256") != _digest(grant)
        or any(receipt.get(k) != expected[k] for k in ("update_id", "candidate_sha", "attempt"))
        or type(value["decided_at"]) not in (int, float) or not math.isfinite(value["decided_at"])
        or not grant["issued_at"] <= receipt["consumed_at"] <= value["decided_at"] < grant["deadline"]
        or value["decided_at"] > time.time()
        or value["system_gate"].get("worker_pid") != grant["worker_pid"]
        or value["system_gate"].get("candidate_sha") != record.request.candidate_sha
        or not grant["issued_at"] <= value["system_gate"].get("verified_at", 0) <= value["decided_at"]):
        raise ValueError("commit decision does not match the accepted update")
    journal = read_journal(transaction)
    if journal["phase"] not in {"activated", "committing", "committed"}:
        raise ValueError("commit transaction phase changed")
    if {k: v for k, v in journal.items() if k != "phase"} != value["transaction_identity"]:
        raise ValueError("commit transaction identity changed")
    return value, journal


def begin_commit(store, update_id: str, transaction: Path, installer_sha256: str, receipt: dict, gate: dict) -> dict:
    """Write once, after both gates passed and before installer backup deletion."""
    with store._locked():
        record = store._load_unlocked(update_id)
        grant = load_grant(store, record)
        if commit_pending(store, record):
            value, _ = load_commit(store, record, transaction, installer_sha256)
            if value["result_sha256"] != _digest(receipt):
                raise ValueError("commit result changed")
            return value
        journal = read_journal(transaction)
        if journal["phase"] != "activated" or receipt.get("verdict") != "pass":
            raise ValueError("commit requires an activated transaction and accepted result")
        now = time.time()
        if now >= grant["deadline"] or gate["worker_pid"] != grant["worker_pid"]:
            raise ValueError("commit authorization expired or worker changed")
        value = dict(schema=1, update_id=update_id, candidate_sha=record.request.candidate_sha,
                     attempt=record.state.attempt, transaction_dir=str(transaction), installer_sha256=installer_sha256,
                     grant_sha256=_digest(grant), result_sha256=_digest(receipt), system_gate=gate, decided_at=now,
                     transaction_identity={k: v for k, v in journal.items() if k != "phase"})
        value["signature"] = _sign(value, grant["token"])
        store._write_json(_path(store, record), value)
        load_commit(store, record, transaction, installer_sha256)
        return value
