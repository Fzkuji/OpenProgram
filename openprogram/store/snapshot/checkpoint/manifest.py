"""Manifest read/write for one turn's backup directory.

A manifest is a flat JSON file ``manifest.json`` next to the backup
blobs. Each entry binds a backup basename to a record describing the
original file's state at turn-start::

    {
      "backed_at": 1735000000.123,
      "files": {
        "<hash>_foo.py":  {"path": "/abs/path/to/foo.py",  "pre_existing": true},
        "<hash>_bar.json": {"path": "/abs/path/to/bar.json", "pre_existing": false}
      }
    }

``pre_existing=false`` means the path didn't exist at turn-start —
the agent is about to create it for the first time. Restoring such
a turn means deleting that file, not copying a backup back.

JSON over a pickle so a human can inspect / hand-edit it and a future
process in a different runtime can still read it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def _empty() -> dict:
    return {"backed_at": 0.0, "files": {}}


def load(manifest_path: Path) -> dict:
    """Read manifest from disk. Missing or corrupt file → empty."""
    if not manifest_path.exists():
        return _empty()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict) or "files" not in data:
        return _empty()
    return data


def save(manifest_path: Path, manifest: dict) -> None:
    """Atomically write manifest. tmp + rename so a crash mid-write
    never leaves a half-parsed JSON behind."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(manifest_path)


def record(
    manifest_path: Path,
    backup_basename: str,
    original_path: str,
    pre_existing: bool,
) -> None:
    """Idempotent: skip if an entry for this basename already exists.
    The first record per (turn, file) wins — that's the "pre-turn"
    state we want to preserve."""
    m = load(manifest_path)
    files = m.setdefault("files", {})
    if backup_basename in files:
        return
    files[backup_basename] = {
        "path": original_path,
        "pre_existing": bool(pre_existing),
    }
    if not m.get("backed_at"):
        m["backed_at"] = time.time()
    save(manifest_path, m)


def has(manifest_path: Path, backup_basename: str) -> bool:
    return backup_basename in load(manifest_path).get("files", {})


def entries(manifest_path: Path) -> list[tuple[str, dict]]:
    """Pairs of (backup_basename, entry_dict). entry_dict has keys
    ``path`` (str) and ``pre_existing`` (bool)."""
    return list(load(manifest_path).get("files", {}).items())
