"""Atomic persistence for one turn's exact file-mutation receipts."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _empty() -> dict:
    return {"version": 2, "backed_at": 0.0, "files": {}}


def load(manifest_path: Path) -> dict:
    """Read a manifest. Missing or corrupt data is treated as empty."""
    if not manifest_path.exists():
        return _empty()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return _empty()
    data.setdefault("version", 1)
    data.setdefault("backed_at", 0.0)
    return data


def save(manifest_path: Path, value: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, manifest_path)
    try:
        directory = os.open(manifest_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass


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
    if existing and existing.get("before"):
        before = existing["before"]
        pre_existing = bool(existing.get("pre_existing"))
        recoverability = existing.get("recoverability", recoverability)
        unavailable_reason = existing.get("unavailable_reason")
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
) -> None:
    value = load(manifest_path)
    entry = value.get("files", {}).get(backup_basename)
    if not entry:
        raise KeyError(f"no prepared mutation for {backup_basename}")
    entry.update({
        "status": "committed",
        "operation": operation,
        "after": after,
        "stats": stats,
        "diff_state": diff_state,
        "committed_at": time.time(),
    })
    value["version"] = 2
    save(manifest_path, value)


def abort(manifest_path: Path, backup_basename: str, error: str | None = None) -> None:
    value = load(manifest_path)
    entry = value.get("files", {}).get(backup_basename)
    if not entry or entry.get("status") == "committed":
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
