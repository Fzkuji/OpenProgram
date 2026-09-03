"""Durable admission gate while an approved update waits for quiescence."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import time
from typing import Iterator

from openprogram.self_update.store import SelfUpdateStore
from openprogram.store.session.git_session import atomic_write_text


_ALLOWED_SOURCE = "self_update_verify"


def _path(store: SelfUpdateStore) -> Path:
    return store.root / "maintenance.json"


def enter_maintenance(update_id: str) -> None:
    store = SelfUpdateStore()
    payload = json.dumps(
        {"schema": 1, "update_id": update_id, "entered_at": time.time()},
        sort_keys=True,
    ) + "\n"
    with store._locked():  # one lock domain with active update state
        record = store._load_unlocked(update_id)
        if record.state.phase.value != "ready":
            raise RuntimeError("maintenance requires a ready self-update")
        path = _path(store)
        if path.is_symlink():
            raise RuntimeError("maintenance state must not be a symbolic link")
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("update_id") != update_id:
                raise RuntimeError("maintenance is owned by another self-update")
            return
        atomic_write_text(path, payload)


def leave_maintenance(update_id: str) -> None:
    store = SelfUpdateStore()
    with store._locked():
        path = _path(store)
        if path.is_symlink():
            raise RuntimeError("maintenance state must not be a symbolic link")
        if not path.exists():
            return
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("maintenance state is unreadable") from exc
        if current.get("update_id") != update_id:
            raise RuntimeError("maintenance is owned by another self-update")
        path.unlink()


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
    if path.is_symlink():
        return True
    if not path.exists():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        valid = (
            isinstance(value, dict)
            and set(value) == {"schema", "update_id", "entered_at"}
            and value.get("schema") == 1
            and isinstance(value.get("update_id"), str)
        )
        if not valid:
            raise ValueError("invalid maintenance state")
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return True


__all__ = [
    "enter_maintenance", "leave_maintenance", "maintenance_blocks", "turn_admission",
]
