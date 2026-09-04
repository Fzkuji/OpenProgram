"""Durable admission gate while an approved update waits for quiescence."""

from __future__ import annotations

from contextlib import contextmanager
import math
from pathlib import Path
import time
from typing import Iterator

from openprogram.self_update.store import SelfUpdateStore
from openprogram.self_update.types import _validate_update_id


_ALLOWED_SOURCE = "self_update_verify"


def _path(store: SelfUpdateStore) -> Path:
    return store.root / "maintenance.json"


def load_maintenance(store: SelfUpdateStore) -> dict | None:
    """Read the private owner marker; callers serialize decisions with the store."""
    from .verification_channel import _read
    path = _path(store)
    if path.is_symlink():
        raise RuntimeError("maintenance state must not be a symbolic link")
    try:
        value = _read(path)
    except FileNotFoundError:
        return None
    if (set(value) != {"schema", "update_id", "entered_at"}
        or type(value["schema"]) is not int or value["schema"] != 1
        or type(value["entered_at"]) not in (int, float)
        or not math.isfinite(value["entered_at"]) or not 0 <= value["entered_at"] <= time.time()):
        raise ValueError("invalid maintenance state")
    _validate_update_id(value["update_id"])
    return value


def enter_maintenance(update_id: str) -> None:
    store = SelfUpdateStore()
    with store._locked():  # one lock domain with active update state
        record = store._load_unlocked(update_id)
        if record.state.phase.value != "ready":
            raise RuntimeError("maintenance requires a ready self-update")
        current = load_maintenance(store)
        if current is not None:
            if current.get("update_id") != update_id:
                raise RuntimeError("maintenance is owned by another self-update")
            return
        store._write_json(_path(store), {"schema": 1, "update_id": update_id, "entered_at": time.time()})


def leave_maintenance(update_id: str) -> None:
    store = SelfUpdateStore()
    with store._locked():
        _leave_maintenance_unlocked(store, update_id)


def _leave_maintenance_unlocked(store: SelfUpdateStore, update_id: str) -> None:
    current = load_maintenance(store)
    if current is None:
        return
    if current.get("update_id") != update_id:
        raise RuntimeError("maintenance is owned by another self-update")
    _path(store).unlink()
    store._fsync_directory(store.root)


@contextmanager
def turn_admission(source: str) -> Iterator[bool]:
    """Keep maintenance entry atomic with persistence of a running turn."""
    store = SelfUpdateStore()
    with store._locked():
        yield not maintenance_blocks(source)


def maintenance_blocks(source: str) -> bool:
    if source == _ALLOWED_SOURCE:
        return False
    store = SelfUpdateStore()
    path = _path(store)
    return path.exists() or path.is_symlink()


__all__ = [
    "enter_maintenance", "leave_maintenance", "maintenance_blocks", "turn_admission",
]
