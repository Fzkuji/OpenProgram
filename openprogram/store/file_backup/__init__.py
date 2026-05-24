"""Per-session file-backup store.

Records each file's pre-turn state so the user can revert any single
turn's edits without depending on the user project being a git repo
(gitignored files would otherwise be invisible to revert). Mirrors
Claude Code's ``fileHistory.ts`` in spirit, restructured into small
modules so each piece is reviewable on its own.

Modules:

* ``paths`` — directory layout + backup-filename hashing.
* ``manifest`` — read/write the per-turn ``manifest.json``.
* ``store.BackupStore`` — public class: ``backup_before_edit`` +
  ``restore_turn`` + ``list_backed_paths``.
* ``gc`` — evict old turn directories beyond a soft cap.

Typical usage from a file-editing tool::

    from openprogram.store.file_backup import BackupStore
    store = BackupStore(session_dir)
    store.backup_before_edit(turn_id, abs_path)
    # ... now safe to overwrite abs_path ...
"""
from .store import BackupStore
from .gc import evict_old as gc_evict_old, MAX_TURNS

__all__ = [
    "BackupStore",
    "gc_evict_old",
    "MAX_TURNS",
]
