"""Turn-context helpers used by trusted file-mutating tools."""
from __future__ import annotations

import os

from .store import CheckpointStore


def _active_checkpoint() -> tuple[CheckpointStore, str] | None:
    from openprogram.store import _current_turn_id, _store

    shim = _store.get()
    turn_id = _current_turn_id.get()
    if shim is None or not turn_id:
        return None
    return CheckpointStore(shim.store._session_dir(shim.session_id)), turn_id


def checkpoint_before_edit(abs_path: str, content_src: str | None = None) -> bool:
    """Persist a prepared receipt before a trusted mutator writes."""
    if not abs_path or not os.path.isabs(abs_path):
        return False
    active = _active_checkpoint()
    if active is None:
        return False
    store, turn_id = active
    store.backup_before_edit(turn_id, abs_path, content_src=content_src)
    return True


def checkpoint_after_edit(abs_path: str, operation: str | None = None) -> bool:
    """Commit a prepared receipt after the filesystem mutation succeeds."""
    if not abs_path or not os.path.isabs(abs_path):
        return False
    active = _active_checkpoint()
    if active is None:
        return False
    store, turn_id = active
    store.commit_after_edit(turn_id, abs_path, operation=operation)
    return True


def checkpoint_abort_edit(abs_path: str, error: str | None = None) -> None:
    active = _active_checkpoint()
    if active is None or not abs_path:
        return
    store, turn_id = active
    store.abort_edit(turn_id, abs_path, error)
