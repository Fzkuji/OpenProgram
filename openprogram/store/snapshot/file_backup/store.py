"""BackupStore — per-session file-backup orchestrator.

Two operations cover the lifecycle:

  * ``backup_before_edit(turn_id, abs_path)`` — call BEFORE any
    write/edit of ``abs_path`` in this turn. Idempotent: only the
    first call per ``(turn, path)`` actually copies; later calls bail
    via the manifest. The "before" semantics preserve the file's
    state when the turn started, not after some intermediate write.
  * ``restore_turn(turn_id)`` — undo all of this turn's file edits
    by copying each backup back to its original path. Files the
    agent CREATED during the turn (no pre-existing version) are
    deleted on restore.

Every backup is a full ``shutil.copy2`` of the original. We
deliberately avoid hardlinking: most editor / tool write paths use
``open(w)``, which truncates the inode in place; a hardlink would
share that inode and lose the original contents. Disk cost is
linear in number-of-files × turns; the GC module caps retained
turns to keep it bounded.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import manifest
from .paths import path_basename, turn_backup_dir, turn_manifest_path


class BackupStore:
    """Per-session backup store rooted under the session's git repo."""

    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)

    # ── Write side ────────────────────────────────────────────────

    def backup_before_edit(self, turn_id: str, abs_path: str) -> None:
        """Idempotent backup. Captures the file's state pre-edit;
        records ``pre_existing=False`` if the path doesn't exist yet
        so ``restore_turn`` knows to delete-instead-of-restore."""
        if not turn_id or not abs_path:
            return
        backup_name = path_basename(abs_path)
        man_path = turn_manifest_path(self.session_dir, turn_id)
        if manifest.has(man_path, backup_name):
            return

        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        backup_dir.mkdir(parents=True, exist_ok=True)

        src = Path(abs_path)
        if not src.exists():
            manifest.record(man_path, backup_name, abs_path, pre_existing=False)
            return

        dst = backup_dir / backup_name
        if not self._copy_file(src, dst):
            return
        manifest.record(man_path, backup_name, abs_path, pre_existing=True)

    @staticmethod
    def _copy_file(src: Path, dst: Path) -> bool:
        """Full copy via shutil.copy2. We intentionally do NOT
        hardlink: the agent's edit path is typically ``open(w)`` →
        ``O_TRUNC`` which truncates the inode in place; any hardlink
        sharing that inode would see the truncation too, defeating
        the backup. ``copy2`` is the safe choice. Returns False if
        the OS rejects the copy (perm error etc)."""
        try:
            shutil.copy2(src, dst)
            return True
        except OSError:
            return False

    # ── Read / restore side ──────────────────────────────────────

    def restore_turn(self, turn_id: str) -> list[str]:
        """Restore every file this turn touched to its pre-turn state.

        For ``pre_existing=True`` entries: copy backup back to the
        original path (atomic-ish via tmp + rename).
        For ``pre_existing=False`` entries (agent CREATED this file
        during the turn): delete the file so the path is gone again.

        Returns the list of paths actually restored or removed. Failure
        on any single file is logged but doesn't abort the rest —
        partial restore is more useful than no restore.
        """
        restored: list[str] = []
        man_path = turn_manifest_path(self.session_dir, turn_id)
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        for backup_name, entry in manifest.entries(man_path):
            original = entry.get("path") or ""
            pre_existing = bool(entry.get("pre_existing"))
            if not original:
                continue
            try:
                if not pre_existing:
                    if Path(original).exists():
                        Path(original).unlink()
                        restored.append(original)
                    continue
                src = backup_dir / backup_name
                if not src.exists():
                    continue
                dst = Path(original)
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_suffix(dst.suffix + ".restore.tmp")
                shutil.copy2(src, tmp)
                tmp.replace(dst)
                restored.append(original)
            except OSError:
                continue
        return restored

    # ── Inspection ───────────────────────────────────────────────

    def list_backed_paths(self, turn_id: str) -> list[str]:
        """Original paths this turn captured. Includes both
        pre-existing files and freshly-created ones (the caller may
        want either)."""
        man_path = turn_manifest_path(self.session_dir, turn_id)
        return [e.get("path", "") for _, e in manifest.entries(man_path) if e.get("path")]
