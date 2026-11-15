"""Durable admission gate while a self-update switches the running code.

On the ``versions`` backend the marker carries a ``deadline``: admission closes
only from step 3 of activation until step 11, and an expired marker is ignored,
so a dead controller cannot close admission forever. ``legacy_app`` writes the
older marker, without a deadline, from ``ready``.
"""

from __future__ import annotations

from contextlib import contextmanager
import math
from pathlib import Path
import time
from typing import Iterator

from openprogram.operations.self_update.store import SelfUpdateStore
from openprogram.operations.self_update.types import PROTOCOL_VERSION, _validate_update_id


# The verifier and the post-switch turn check run while admission is closed.
_ALLOWED_SOURCES = frozenset({"self_update_verify", "self_update_turn_check"})
_KEYS = {"schema", "update_id", "entered_at"}
_OPTIONAL_KEYS = {"protocol", "deadline"}


def _path(store: SelfUpdateStore) -> Path:
    return store.root / "maintenance.json"


def load_maintenance(store: SelfUpdateStore) -> dict | None:
    """Read the private owner marker; callers serialize decisions with the store."""
    from ..verification.verification_channel import _read
    path = _path(store)
    if path.is_symlink():
        raise RuntimeError("maintenance state must not be a symbolic link")
    try:
        value = _read(path)
    except FileNotFoundError:
        return None
    keys = set(value) & (_KEYS | _OPTIONAL_KEYS) if "protocol" in value else set(value)
    if (not _KEYS <= keys or keys - _KEYS - _OPTIONAL_KEYS
        or type(value["schema"]) is not int or value["schema"] not in {1, 2}
        or type(value["entered_at"]) not in (int, float)
        or not math.isfinite(value["entered_at"]) or not 0 <= value["entered_at"] <= time.time()
        or ("deadline" in value and (type(value["deadline"]) not in (int, float)
                                     or not math.isfinite(value["deadline"])))):
        raise ValueError("invalid maintenance state")
    _validate_update_id(value["update_id"])
    return value


def expired(marker: dict, *, now: float | None = None) -> bool:
    """Whether a marker's deadline has passed; a marker without one never expires."""
    deadline = marker.get("deadline")
    return deadline is not None and (time.time() if now is None else now) >= deadline


def closes_admission(marker: dict | None) -> bool:
    return marker is not None and not expired(marker)


def holds_turns(marker: dict | None) -> bool:
    """Whether arrivals wait and run afterwards, rather than being turned away."""
    return closes_admission(marker) and "deadline" in (marker or {})


def enter_maintenance(update_id: str, *, deadline: float | None = None) -> None:
    store = SelfUpdateStore()
    with store._locked():  # one lock domain with active update state
        record = store._load_unlocked(update_id)
        allowed = "activating" if deadline is not None else "ready"
        if record.state.phase.value != allowed:
            raise RuntimeError(f"maintenance requires an update in {allowed}")
        current = load_maintenance(store)
        if current is not None:
            if current.get("update_id") != update_id:
                raise RuntimeError("maintenance is owned by another self-update")
            if deadline is None or current.get("deadline") == deadline:
                return
        value = {"schema": 1, "update_id": update_id, "entered_at": time.time()}
        if deadline is not None:
            value.update(schema=2, protocol=PROTOCOL_VERSION, deadline=deadline)
        store._write_json(_path(store), value)


def leave_maintenance(update_id: str) -> None:
    store = SelfUpdateStore()
    with store._locked():
        _leave_maintenance_unlocked(store, update_id)


def _leave_maintenance_unlocked(store: SelfUpdateStore, update_id: str) -> None:
    current = load_maintenance(store)
    if current is not None and current.get("update_id") != update_id:
        raise RuntimeError("maintenance is owned by another self-update")
    # A hot swap never entered maintenance but still needs its diagnosis.
    from ..repair.diagnosis import prepare_after_rollback
    try:
        prepare_after_rollback(store, store._load_unlocked(update_id))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Post-rollback diagnosis could not be prepared")
    if current is None:
        return
    _path(store).unlink()
    store._fsync_directory(store.root)


@contextmanager
def turn_admission(source: str) -> Iterator[bool]:
    """Keep maintenance entry atomic with persistence of a running turn."""
    store = SelfUpdateStore()
    with store._locked():
        marker = load_maintenance(store)
        admitted = not closes_admission(marker) or source in _ALLOWED_SOURCES
        if not admitted:
            # A claim/foreground admission can precede maintenance while its
            # producer reaches the dispatcher afterwards. Let that exact
            # execution reach its cooperative checkpoint instead of failing it.
            from openprogram.runtime.execution.run_control import (
                get_current_execution_id,
            )
            from openprogram.runtime.execution.store import default_store
            execution_id = get_current_execution_id()
            execution = default_store().get_execution(execution_id) if execution_id else None
            admitted = (execution is not None
                        and execution.created_at <= marker["entered_at"]
                        and execution.current_attempt_id is not None
                        and execution.status.value in {"running", "pausing"})
        yield admitted


def maintenance_blocks(source: str) -> bool:
    if source in _ALLOWED_SOURCES:
        return False
    store = SelfUpdateStore()
    path = _path(store)
    if not path.exists() and not path.is_symlink():
        return False
    try:
        return closes_admission(load_maintenance(store))
    except (OSError, ValueError, RuntimeError):
        return True  # An unreadable marker stays closed, as it always has.


__all__ = [
    "closes_admission", "enter_maintenance", "expired", "holds_turns", "leave_maintenance",
    "load_maintenance", "maintenance_blocks", "turn_admission",
]


def claim_job(governor, *, owner_instance_id, excluded_sessions, only_job_id, store=None):
    """Serialize maintenance entry with a durable resource claim.

    ``store`` is the runner's own self-update store, fixed when the runner
    started, as its governor and execution store are; without it the
    profile's current one is used.
    """
    store = store if store is not None else SelfUpdateStore()
    with store._locked():
        marker = load_maintenance(store)
        if closes_admission(marker):
            record = store._load_unlocked(marker["update_id"])
            dispatch = record.state.dispatch
            if record.state.phase.value != "verifying" or dispatch is None:
                return None
            if only_job_id is not None and only_job_id != dispatch.job_id:
                return None
            only_job_id = dispatch.job_id
        return governor.claim_next(owner_instance_id=owner_instance_id,
            excluded_sessions=excluded_sessions, only_job_id=only_job_id)
