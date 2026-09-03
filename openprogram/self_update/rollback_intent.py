"""Durable rollback intent shared by the controller and worker admission."""
from __future__ import annotations

import math
import os
import re
import stat
import time

from .types import UpdatePhase

RECOVERY_SECONDS = 390  # Existing installer timeout (300) plus probe and startup.
_REVISION = re.compile(r"[0-9a-f]{40}(?:-dirty)?")


def load_rollback_intent(store, record) -> dict | None:
    """Read under the store lock when used to authorize Job admission."""
    path = store.root / record.request.update_id / f"rollback-{record.state.attempt}.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 8192:
            raise ValueError("invalid rollback intent file")
        value = store._loads_json(handle.read(8193))
    if not isinstance(value, dict) or set(value) != {
        "schema", "update_id", "candidate_sha", "attempt", "previous_revision",
        "started_at", "deadline", "error",
    }:
        raise ValueError("malformed rollback intent")
    if (
        type(value["schema"]) is not int or value["schema"] != 1
        or value["update_id"] != record.request.update_id
        or value["candidate_sha"] != record.request.candidate_sha
        or type(value["attempt"]) is not int or value["attempt"] != record.state.attempt
        or not isinstance(value["previous_revision"], str)
        or not _REVISION.fullmatch(value["previous_revision"])
        or value["previous_revision"] != record.state.detail.get("previous_system_gate", {}).get("candidate_sha")
        or not isinstance(value["error"], str) or len(value["error"]) > 2000
        or any(type(value[key]) not in (int, float) or not math.isfinite(value[key])
               for key in ("started_at", "deadline"))
        or value["started_at"] < record.request.created_at
        or value["started_at"] > time.time()
        or value["deadline"] != value["started_at"] + RECOVERY_SECONDS
    ):
        raise ValueError("rollback intent does not match the update")
    return value


def begin_rollback(store, update_id: str, error: str) -> dict:
    with store._locked():
        record = store._load_unlocked(update_id)
        if record.state.phase not in {UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING}:
            raise ValueError("rollback requires an activated update")
        existing = load_rollback_intent(store, record)
        if existing is not None:
            return existing  # Retries never extend the recovery deadline.
        revision = record.state.detail.get("previous_system_gate", {}).get("candidate_sha")
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ValueError("previous live revision is unavailable")
        started = time.time()
        value = dict(schema=1, update_id=update_id, candidate_sha=record.request.candidate_sha,
                     attempt=record.state.attempt, previous_revision=revision,
                     started_at=started, deadline=started + RECOVERY_SECONDS, error=error[:2000])
        store._write_json(store.root / update_id / f"rollback-{record.state.attempt}.json", value)
        return value
