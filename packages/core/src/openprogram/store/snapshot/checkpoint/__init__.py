"""Per-session checkpoint store.

Records each file's pre-turn state so the user can undo any single
turn's edits without depending on the user project being a git repo
(gitignored files would otherwise be invisible to revert). Mirrors
Claude Code's checkpoint mechanism in spirit, restructured into small
modules so each piece is reviewable on its own.

Modules:

* ``paths`` — directory layout + checkpoint-filename hashing.
* ``manifest`` — read/write the per-turn ``manifest.json``.
* ``store.CheckpointStore`` — public class: ``backup_before_edit`` +
  ``restore_turn`` + ``list_backed_paths``.
* ``gc`` — evict old turn directories beyond a soft cap.

Typical usage::

    from openprogram.store.snapshot.checkpoint import CheckpointStore
    store = CheckpointStore(session_dir)
    store.backup_before_edit(turn_id, abs_path)
    # ... now safe to overwrite abs_path ...
"""
from .store import CheckpointStore, BackupStore
from .gc import evict_old as gc_evict_old, MAX_TURNS

__all__ = [
    "CheckpointStore",
    "BackupStore",
    "gc_evict_old",
    "MAX_TURNS",
]
