"""Per-session exact file-mutation journal and recovery snapshots."""
from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import stat
from pathlib import Path

from . import manifest
from .paths import path_basename, turn_backup_dir, turn_manifest_path


_STATS_MAX_BYTES = 1024 * 1024


class MutationJournalError(RuntimeError):
    """A trusted mutation could not be recorded safely."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return f"sha256:{value.hexdigest()}"


def _file_kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    return "special"


def _has_nul(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(8192)


def _line_stats(before: Path | None, after: Path | None) -> tuple[dict, str]:
    paths = [path for path in (before, after) if path is not None]
    if any(path.stat().st_size > _STATS_MAX_BYTES for path in paths):
        binary = any(_has_nul(path) for path in paths)
        return {"added": None, "removed": None, "binary": binary}, (
            "binary" if binary else "large"
        )
    raw_before = before.read_bytes() if before is not None else b""
    raw_after = after.read_bytes() if after is not None else b""
    if b"\0" in raw_before or b"\0" in raw_after:
        return {"added": None, "removed": None, "binary": True}, "binary"
    old = raw_before.decode("utf-8", errors="replace").splitlines()
    new = raw_after.decode("utf-8", errors="replace").splitlines()
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=old, b=new, autojunk=False,
    ).get_opcodes():
        if tag in {"insert", "replace"}:
            added += j2 - j1
        if tag in {"delete", "replace"}:
            removed += i2 - i1
    return {"added": added, "removed": removed, "binary": False}, "available"


class CheckpointStore:
    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)

    def _capture_regular(self, source: Path, destination: Path) -> dict:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, destination, follow_symlinks=False)
            info = destination.stat()
            return {
                "kind": "regular",
                "digest": _digest(destination),
                "blob_ref": destination.name,
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "size": info.st_size,
            }
        except OSError as exc:
            raise MutationJournalError(f"cannot snapshot {source}: {exc}") from exc

    def backup_before_edit(
        self,
        turn_id: str,
        abs_path: str,
        *,
        content_src: str | Path | None = None,
    ) -> None:
        if not turn_id or not abs_path:
            return
        backup_name = path_basename(abs_path)
        manifest_path = turn_manifest_path(self.session_dir, turn_id)
        target = Path(abs_path)
        try:
            target_stat = os.lstat(target)
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            raise MutationJournalError(f"cannot inspect {target}: {exc}") from exc

        if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
            raise MutationJournalError(
                f"unsafe file type for exact mutation: {_file_kind(target_stat.st_mode)}",
            )
        if target_stat is not None and target_stat.st_nlink != 1:
            raise MutationJournalError(
                f"hardlinked file has {target_stat.st_nlink} links",
            )
        if manifest.has(manifest_path, backup_name):
            return
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        backup_dir.mkdir(parents=True, exist_ok=True)

        pre_existing = target_stat is not None
        recoverability = "exact"
        unavailable_reason = None
        if not pre_existing:
            before = {"kind": "absent"}
        else:
            source = Path(content_src) if content_src is not None else target
            try:
                source_stat = os.lstat(source)
            except FileNotFoundError:
                source_stat = None
            if source_stat is None or not stat.S_ISREG(source_stat.st_mode):
                before = {"kind": "unavailable"}
                recoverability = "unavailable"
                unavailable_reason = "missing_preimage"
            else:
                before = self._capture_regular(source, backup_dir / backup_name)

        manifest.record_prepared(
            manifest_path,
            backup_name,
            abs_path,
            pre_existing=pre_existing,
            before=before,
            recoverability=recoverability,
            unavailable_reason=unavailable_reason,
        )

    def commit_after_edit(
        self, turn_id: str, abs_path: str, *, operation: str | None = None,
    ) -> None:
        if not turn_id or not abs_path:
            return
        backup_name = path_basename(abs_path)
        manifest_path = turn_manifest_path(self.session_dir, turn_id)
        value = manifest.load(manifest_path)
        entry = value.get("files", {}).get(backup_name)
        if not entry:
            raise MutationJournalError(f"no prepared mutation for {abs_path}")
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        target = Path(abs_path)
        try:
            target_stat = os.lstat(target)
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            raise MutationJournalError(f"cannot inspect {target}: {exc}") from exc

        after_blob: Path | None = None
        if target_stat is None:
            after = {"kind": "absent"}
        elif stat.S_ISREG(target_stat.st_mode):
            after_blob = backup_dir / f"{backup_name}.after"
            after = self._capture_regular(target, after_blob)
        else:
            after = {"kind": _file_kind(target_stat.st_mode)}

        before = entry.get("before") or {
            "kind": "regular" if entry.get("pre_existing") else "absent",
        }
        before_blob = (
            backup_dir / str(before.get("blob_ref"))
            if before.get("kind") == "regular" and before.get("blob_ref")
            else None
        )
        if before.get("kind") == "absent" and after.get("kind") == "regular":
            canonical_operation = "create"
        elif before.get("kind") == "regular" and after.get("kind") == "absent":
            canonical_operation = "delete"
        else:
            canonical_operation = operation or "modify"
            if canonical_operation in {"write", "edit", "update", "add"}:
                canonical_operation = "modify"
        stats, diff_state = _line_stats(before_blob, after_blob)
        manifest.commit(
            manifest_path,
            backup_name,
            operation=canonical_operation,
            after=after,
            stats=stats,
            diff_state=diff_state,
        )

    def abort_edit(self, turn_id: str, abs_path: str, error: str | None = None) -> None:
        if turn_id and abs_path:
            manifest.abort(
                turn_manifest_path(self.session_dir, turn_id),
                path_basename(abs_path),
                error,
            )

    def list_mutations(self, turn_id: str) -> list[dict]:
        rows: list[dict] = []
        for _backup_name, entry in manifest.entries(
            turn_manifest_path(self.session_dir, turn_id),
        ):
            if entry.get("status") == "committed":
                rows.append(dict(entry))
        return rows

    def restore_turn(self, turn_id: str) -> list[str]:
        """Legacy best-effort restore; task B replaces this execution path."""
        restored: list[str] = []
        manifest_path = turn_manifest_path(self.session_dir, turn_id)
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        for backup_name, entry in manifest.entries(manifest_path):
            if entry.get("status") == "aborted":
                continue
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
                source = backup_dir / backup_name
                if not source.exists():
                    continue
                destination = Path(original)
                destination.parent.mkdir(parents=True, exist_ok=True)
                tmp = destination.with_suffix(destination.suffix + ".restore.tmp")
                shutil.copy2(source, tmp)
                tmp.replace(destination)
                restored.append(original)
            except OSError:
                continue
        return restored

    def list_backed_paths(self, turn_id: str) -> list[str]:
        return [
            entry.get("path", "")
            for _name, entry in manifest.entries(
                turn_manifest_path(self.session_dir, turn_id),
            )
            if entry.get("path") and entry.get("status") != "aborted"
        ]


BackupStore = CheckpointStore
