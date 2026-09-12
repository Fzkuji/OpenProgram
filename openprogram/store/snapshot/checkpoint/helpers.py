"""Turn-context helpers used by trusted file-mutating tools."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from .store import CheckpointStore


@contextmanager
def _locked_checkpoint():
    from openprogram.store import _current_turn_id, _store
    from openprogram.store.session.session_lock import session_interprocess_lock

    shim = _store.get()
    turn_id = _current_turn_id.get()
    if shim is None or not turn_id:
        yield None
        return
    with session_interprocess_lock(
        shim.session_id,
        root=shim.store.root_path if getattr(shim.store, "_explicit_root", False)
        else None,
    ):
        yield CheckpointStore(shim.store._session_dir(shim.session_id)), turn_id, shim


def _project_locator(shim, abs_path: str) -> dict | None:
    """Bind only to a registered project whose authoritative root owns path."""
    from openprogram.store.project import project_for_session
    from openprogram.store.project.location import bound_execution_state

    project = project_for_session(shim.session_id)
    if project is None or not getattr(project, "id", "") or not getattr(project, "path", ""):
        return None
    if bound_execution_state(project) is not None:
        return None
    root = Path(str(project.path)).expanduser()
    try:
        root = Path(os.path.normpath(os.path.abspath(root)))
        target = Path(os.path.normpath(os.path.abspath(abs_path)))
        relative = target.relative_to(root)
        resolved_root = root.resolve()
        resolved_target = target.resolve(strict=False)
        resolved_target.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if not root.is_dir() or not relative.parts:
        return None
    return {
        "project_id": str(project.id),
        "path": relative.as_posix(),
        "recorded_root": str(root),
        "directory_identity": str(getattr(project, "directory_identity", "") or ""),
        "location_revision": int(getattr(project, "location_revision", 0) or 0),
    }


def _register(shim, turn_id: str, *, propagate: bool) -> None:
    try:
        from openprogram.store.document_history import DocumentHistory
        DocumentHistory().register_model_turn(
            shim.session_id, turn_id, session_store=shim.store,
        )
    except Exception:
        if propagate:
            raise


def checkpoint_before_edit(abs_path: str, content_src: str | None = None) -> bool:
    """Persist a prepared receipt before a trusted mutator writes."""
    if not abs_path or not os.path.isabs(abs_path):
        return False
    with _locked_checkpoint() as active:
        if active is None:
            return False
        store, turn_id, shim = active
        store.backup_before_edit(
            turn_id, abs_path, content_src=content_src,
            project_locator=_project_locator(shim, abs_path),
        )
        _register(shim, turn_id, propagate=True)
        return True


def checkpoint_after_edit(abs_path: str, operation: str | None = None) -> bool:
    """Commit a prepared receipt after the filesystem mutation succeeds."""
    if not abs_path or not os.path.isabs(abs_path):
        return False
    with _locked_checkpoint() as active:
        if active is None:
            return False
        store, turn_id, shim = active
        store.commit_after_edit(turn_id, abs_path, operation=operation)
        _register(shim, turn_id, propagate=True)
        return True


def checkpoint_abort_edit(abs_path: str, error: str | None = None) -> None:
    if not abs_path:
        return
    with _locked_checkpoint() as active:
        if active is None:
            return
        store, turn_id, shim = active
        store.abort_edit(turn_id, abs_path, error)
        _register(shim, turn_id, propagate=False)
