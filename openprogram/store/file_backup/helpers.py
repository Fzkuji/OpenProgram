"""Convenience helper for file-mutating tools.

A tool typically only knows the absolute path it's about to write.
Resolving "which session + which turn + where does that session live
on disk" requires walking three ContextVars + the SessionStore. This
helper hides that lookup so each tool's pre-write hook is a single
line::

    from openprogram.store.file_backup.helpers import backup_for_current_turn
    backup_for_current_turn(file_path)

It's a graceful no-op when there is no active session/turn (e.g. unit
tests calling the tool function directly), so tools stay usable
outside a dispatcher-driven turn.
"""
from __future__ import annotations

from .store import BackupStore


def backup_for_current_turn(abs_path: str) -> None:
    """Snapshot ``abs_path`` into the current turn's backup dir.

    Silent no-op when:
      * there is no active session (``_store`` ContextVar unset);
      * there is no active turn (``_current_turn_id`` unset);
      * the path is not absolute;
      * any lookup fails — we don't want a backup glitch to crash the
        actual edit.
    """
    if not abs_path:
        return
    try:
        from openprogram.store import _store, _current_turn_id

        shim = _store.get()
        turn_id = _current_turn_id.get()
        if shim is None or not turn_id:
            return
        session_dir = shim.store._session_dir(shim.session_id)
        BackupStore(session_dir).backup_before_edit(turn_id, abs_path)
    except Exception:
        return
