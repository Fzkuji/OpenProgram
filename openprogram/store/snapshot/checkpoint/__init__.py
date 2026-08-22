"""Per-session checkpoint store.

Trusted file mutators persist a prepared receipt before writing and commit
the exact before/after digest, snapshots, operation, and bounded line stats
after success. Ordinary Bash and external writes never enter this journal.

Modules:

* ``paths`` — directory layout + checkpoint-filename hashing.
* ``manifest`` — read/write the per-turn ``manifest.json``.
* ``store.CheckpointStore`` — prepare/commit/inspect mutation receipts and
  the legacy restore entry point used until the transactional planner lands.
* ``gc`` — evict old turn directories beyond a soft cap.

Typical usage::

    from openprogram.store.snapshot.checkpoint import CheckpointStore
    store = CheckpointStore(session_dir)
    store.backup_before_edit(turn_id, abs_path)
    # ... mutate abs_path ...
    store.commit_after_edit(turn_id, abs_path, operation="modify")
"""
from .store import CheckpointStore, BackupStore, MutationJournalError
from .gc import evict_old as gc_evict_old, MAX_TURNS

__all__ = [
    "CheckpointStore",
    "BackupStore",
    "MutationJournalError",
    "gc_evict_old",
    "MAX_TURNS",
]
