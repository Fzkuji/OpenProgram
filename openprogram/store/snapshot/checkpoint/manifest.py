"""Atomic persistence for one turn's exact file-mutation receipts."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


def _empty() -> dict:
    return {"version": 2, "backed_at": 0.0, "files": {}}


class ManifestCorruptionError(ValueError):
    """Existing history cannot be interpreted without discarding evidence."""


def _valid_entry(entry: object, version: int) -> bool:
    if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
            or not entry["path"] or not isinstance(entry.get("pre_existing"), bool)):
        return False
    if version == 1 and "status" not in entry:
        return True  # Legacy before-only records have no commit state.
    if entry.get("status") not in {"prepared", "committed", "aborted"}:
        return False
    if "pending" in entry and not isinstance(entry["pending"], bool):
        return False
    before, after = entry.get("before"), entry.get("after")
    if not _valid_state(before):
        return False
    if after is None:
        return entry["status"] != "committed"
    return _valid_state(after)


def _valid_state(state: object) -> bool:
    if not isinstance(state, dict) or state.get("kind") not in {
        "regular", "absent", "unavailable", "symlink", "directory", "special",
    }:
        return False
    # Older before-only snapshots can lack a digest or explicit blob_ref.
    # Such records remain readable; restore preflight determines exactness.
    ref = state.get("blob_ref")
    if ref is not None and (not isinstance(ref, str) or not ref
                            or Path(ref).name != ref or ref in {".", ".."}):
        return False
    return "digest" not in state or isinstance(state["digest"], str)


def load(manifest_path: Path) -> dict:
    """Only a missing manifest is empty; unreadable history is never replaced."""
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ManifestCorruptionError(f"Cannot read mutation history: {manifest_path}") from exc
    if (not isinstance(data, dict) or not isinstance(data.get("files"), dict)
            or data.get("version", 1) not in (1, 2)
            or any(not _valid_entry(entry, data.get("version", 1))
                   for entry in data["files"].values())):
        raise ManifestCorruptionError(f"Invalid or unsupported mutation history: {manifest_path}")
    data.setdefault("version", 1)
    data.setdefault("backed_at", 0.0)
    return data


def save(manifest_path: Path, value: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, manifest_path)
        if os.name != "nt":
            directory = os.open(manifest_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        tmp.unlink(missing_ok=True)


def record_prepared(
    manifest_path: Path,
    backup_basename: str,
    original_path: str,
    *,
    pre_existing: bool,
    before: dict,
    recoverability: str = "exact",
    unavailable_reason: str | None = None,
) -> None:
    """Persist the first pre-turn image before a trusted mutator writes."""
    value = load(manifest_path)
    files = value.setdefault("files", {})
    existing = files.get(backup_basename)
    if existing and existing.get("status") != "aborted":
        return
    files[backup_basename] = {
        "path": original_path,
        "pre_existing": bool(pre_existing),
        "status": "prepared",
        "operation": None,
        "before": before,
        "after": None,
        "stats": None,
        "diff_state": "pending",
        "recoverability": recoverability,
        "unavailable_reason": unavailable_reason,
        "prepared_at": time.time(),
        "committed_at": None,
    }
    value["version"] = 2
    if not value.get("backed_at"):
        value["backed_at"] = time.time()
    save(manifest_path, value)


def commit(
    manifest_path: Path,
    backup_basename: str,
    *,
    operation: str,
    after: dict,
    stats: dict,
    diff_state: str,
    mutation_sequence: int,
) -> None:
    value = load(manifest_path)
    entry = value.get("files", {}).get(backup_basename)
    if not entry:
        raise KeyError(f"no prepared mutation for {backup_basename}")
    entry.pop("pending", None)
    entry.update({
        "status": "committed",
        "operation": operation,
        "after": after,
        "stats": stats,
        "diff_state": diff_state,
        "mutation_sequence": mutation_sequence,
        "committed_at": time.time(),
    })
    value["version"] = 2
    save(manifest_path, value)


def abort(manifest_path: Path, backup_basename: str, error: str | None = None) -> None:
    value = load(manifest_path)
    entry = value.get("files", {}).get(backup_basename)
    if not entry:
        return
    if entry.get("status") == "committed":
        # A writer error may follow a partial write, or another interrupted
        # attempt. Only a successful commit can resolve that uncertainty.
        return
    entry["status"] = "aborted"
    entry["error"] = error
    save(manifest_path, value)


def record(
    manifest_path: Path,
    backup_basename: str,
    original_path: str,
    pre_existing: bool,
) -> None:
    """Compatibility entry point for legacy callers and old fixtures."""
    record_prepared(
        manifest_path,
        backup_basename,
        original_path,
        pre_existing=pre_existing,
        before={"kind": "regular" if pre_existing else "absent"},
    )


def has(manifest_path: Path, backup_basename: str) -> bool:
    entry = load(manifest_path).get("files", {}).get(backup_basename)
    return bool(entry and entry.get("status") != "aborted")


def entries(manifest_path: Path) -> list[tuple[str, dict]]:
    return list(load(manifest_path).get("files", {}).items())


def mark_pending(manifest_path: Path, backup_basename: str) -> None:
    """Retain the published receipt while a subsequent attempt is unfinished."""
    value = load(manifest_path)
    entry = value["files"][backup_basename]
    if not entry.get("pending"):
        entry["pending"] = True
        save(manifest_path, value)
