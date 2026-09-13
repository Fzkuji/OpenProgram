"""Record file mutations and expose their snapshots and history."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from . import capture, file_state, manifest
from .capture import MutationJournalError
from .paths import path_basename, turn_backup_dir, turn_manifest_path


def backup_before_edit(
    session_dir: Path, turn_id: str,
    abs_path: str,
    *,
    content_src: str | Path | None = None,
    project_locator: dict | None = None,
) -> None:
    if not turn_id or not abs_path:
        return
    backup_name = path_basename(abs_path)
    manifest_path = turn_manifest_path(session_dir, turn_id)
    target = Path(abs_path)
    try:
        target_stat = os.lstat(target)
    except FileNotFoundError:
        target_stat = None
    except OSError as exc:
        raise MutationJournalError(f"cannot inspect {target}: {exc}") from exc

    if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
        raise MutationJournalError(
            f"unsafe file type for exact mutation: {file_state._file_kind(target_stat.st_mode)}",
        )
    if target_stat is not None and target_stat.st_nlink != 1:
        raise MutationJournalError(
            f"hardlinked file has {target_stat.st_nlink} links",
        )
    existing = manifest.load(manifest_path).get("files", {}).get(backup_name)
    if existing and existing.get("status") != "aborted":
        if existing.get("status") == "committed":
            manifest.mark_pending(manifest_path, backup_name)
        return
    backup_dir = turn_backup_dir(session_dir, turn_id)
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
            before = capture._capture_regular(source, backup_dir / backup_name)

    manifest.record_prepared(
        manifest_path,
        backup_name,
        abs_path,
        pre_existing=pre_existing,
        before=before,
        recoverability=recoverability,
        unavailable_reason=unavailable_reason,
        project_locator=project_locator,
    )



def commit_after_edit(
    session_dir: Path, turn_id: str, abs_path: str, *, operation: str | None = None,
) -> None:
    if not turn_id or not abs_path:
        return
    backup_name = path_basename(abs_path)
    manifest_path = turn_manifest_path(session_dir, turn_id)
    value = manifest.load(manifest_path)
    entry = value.get("files", {}).get(backup_name)
    if not entry:
        raise MutationJournalError(f"no prepared mutation for {abs_path}")
    backup_dir = turn_backup_dir(session_dir, turn_id)
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
        after = capture._capture_regular(target, after_blob)
        after_blob = backup_dir / after["blob_ref"]
    else:
        after = {"kind": file_state._file_kind(target_stat.st_mode)}

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
    stats, diff_state = capture._line_stats(before_blob, after_blob)
    mutation_sequence = _next_mutation_sequence()
    manifest.commit(
        manifest_path,
        backup_name,
        operation=canonical_operation,
        after=after,
        stats=stats,
        diff_state=diff_state,
        mutation_sequence=mutation_sequence,
    )



def _next_mutation_sequence() -> int:
    """Allocate one durable workspace-wide mutation order value."""
    from openprogram import _compat as fcntl

    from openprogram.paths import get_state_dir

    root = get_state_dir() / "mutation-locks"
    root.mkdir(parents=True, exist_ok=True)
    counter_path = root / "workspace-sequence"
    with (root / "workspace-sequence.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                current = int(counter_path.read_text(encoding="ascii").strip())
            except (FileNotFoundError, OSError, ValueError):
                current = 0
            value = current + 1
            tmp = root / ".workspace-sequence.tmp"
            with tmp.open("w", encoding="ascii") as handle:
                handle.write(str(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, counter_path)
            try:
                self_descriptor = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(self_descriptor)
                finally:
                    os.close(self_descriptor)
            except OSError:
                pass  # Windows has no directory descriptor to sync
            return value
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)



def abort_edit(session_dir: Path, turn_id: str, abs_path: str, error: str | None = None) -> None:
    if turn_id and abs_path:
        manifest.abort(
            turn_manifest_path(session_dir, turn_id),
            path_basename(abs_path),
            error,
        )



def list_file_history(session_dir: Path, turn_id: str) -> list[dict]:
    """Project incomplete intents as unknown, never as successful mutations."""
    rows = []
    for _, entry in manifest.entries(turn_manifest_path(session_dir, turn_id)):
        status = entry.get("status")
        if status == "aborted":
            # Failure does not prove that the tool had no side effect.
            if file_state._state_matches(file_state._inspect_state(entry["path"]), entry.get("before") or {}):
                continue
        elif status not in {"prepared", "committed"}:
            continue
        row = dict(entry)
        if status in {"prepared", "aborted"} or entry.get("pending"):
            row.update(after={"kind": "unavailable"},
                       stats={"added": None, "removed": None, "binary": False},
                       diff_state="unavailable", recoverability="unavailable",
                       unavailable_reason="mutation_incomplete")
        rows.append(row)
    return rows



def restore_turn(session_dir: Path, turn_id: str) -> list[str]:
    """Restore recorded files through the legacy best-effort API."""
    restored: list[str] = []
    manifest_path = turn_manifest_path(session_dir, turn_id)
    backup_dir = turn_backup_dir(session_dir, turn_id)
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
            source = backup_dir / str((entry.get("before") or {}).get("blob_ref") or backup_name)
            if not source.exists():
                continue
            destination = Path(original)
            destination.parent.mkdir(parents=True, exist_ok=True)
            tmp = destination.with_suffix(destination.suffix + ".restore.tmp")
            shutil.copy2(source, tmp)
            mode = (entry.get("before") or {}).get("mode")
            if mode is not None:
                os.chmod(tmp, int(mode, 8))
            tmp.replace(destination)
            restored.append(original)
        except OSError:
            continue
    return restored



def list_backed_paths(session_dir: Path, turn_id: str) -> list[str]:
    return [
        entry.get("path", "")
        for _name, entry in manifest.entries(
            turn_manifest_path(session_dir, turn_id),
        )
        if entry.get("path") and entry.get("status") != "aborted"
    ]



def list_mutations(session_dir: Path, turn_id: str) -> list[dict]:
    rows: list[dict] = []
    for _backup_name, entry in manifest.entries(
        turn_manifest_path(session_dir, turn_id),
    ):
        if entry.get("status") == "committed":
            row = dict(entry)
            if row.get("pending"):
                row.update(recoverability="unavailable", unavailable_reason="mutation_incomplete",
                           diff_state="unavailable")
            rows.append(row)
    return rows

