"""Revert / record helpers — undo + concurrency safety.

Group ③ of ``store/`` (see ``store/README.md`` and
``docs/design/revert-layers.md``). The "undo" layer plus the guard that
keeps it trustworthy.

Contents:
  * ``file_backup/``   — per-turn file snapshots (the "Ctrl+Z" layer):
                         BackupStore.backup_before_edit / restore_turn,
                         plus ``evict_old`` (GC) and the
                         ``backup_for_current_turn`` tool hook.
  * ``read_tracking``  — read-before-edit freshness gate: refuse to write
                         a file the agent never read / that changed on
                         disk since (Claude-Code-style).

``from openprogram.store.revert import read_tracking`` /
``from openprogram.store.revert.file_backup import BackupStore``.
"""
from . import read_tracking
from .file_backup import BackupStore, gc_evict_old, MAX_TURNS

__all__ = [
    "read_tracking",
    "BackupStore",
    "gc_evict_old",
    "MAX_TURNS",
]
