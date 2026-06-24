"""Revert / record helpers — undo + concurrency safety.

Group ③ of ``store/`` (see ``store/README.md`` and
``docs/design/runtime/revert-layers.md``). The "undo" layer plus the guard that
keeps it trustworthy.

Contents:
  * ``checkpoint/``    — per-turn file checkpoints (the "Ctrl+Z" layer):
                         CheckpointStore.backup_before_edit / restore_turn,
                         plus ``evict_old`` (GC) and the
                         ``checkpoint_before_edit`` tool hook.
  * ``read_tracking``  — read-before-edit freshness gate: refuse to write
                         a file the agent never read / that changed on
                         disk since (Claude-Code-style).

``from openprogram.store.snapshot import read_tracking`` /
``from openprogram.store.snapshot.checkpoint import CheckpointStore``.
"""
from . import read_tracking
from .checkpoint import CheckpointStore, BackupStore, gc_evict_old, MAX_TURNS

__all__ = [
    "read_tracking",
    "CheckpointStore",
    "BackupStore",
    "gc_evict_old",
    "MAX_TURNS",
]
