"""One-time migration from Memory Commitments into Scheduler tasks."""

from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil
from typing import Any

from openprogram.programs.tools.jobs.cron import cron as cron_tool

from . import service


def legacy_archive_path() -> Path:
    return Path(cron_tool._resolve_path()).parent / "migrations" / "commitments.jsonl"


def migrate_legacy_commitments(
    *,
    memory_root: str | Path,
    cwd: str | None = None,
    authority: dict[str, Any] | None = None,
) -> int:
    lock_target = f"{legacy_archive_path()}.migration"
    with cron_tool._store_lock(lock_target):
        return _migrate_legacy_commitments(
            memory_root=memory_root, cwd=cwd, authority=authority,
        )


def _migrate_legacy_commitments(
    *,
    memory_root: str | Path,
    cwd: str | None,
    authority: dict[str, Any] | None,
) -> int:
    from openprogram.memory.runtime.commitments import load_commitments

    memory_root = Path(memory_root)
    source = memory_root / "commitments.jsonl"
    if not source.is_file():
        return 0
    rows = load_commitments(memory_root)
    existing = {
        row.get("legacy_commitment_id") for row in service.list_tasks()
    }
    created = 0
    for row in rows:
        if row.get("status") != "open" or row.get("id") in existing:
            continue
        due = row.get("due")
        run_at = None
        enabled = False
        if due:
            local = datetime.strptime(due, "%Y-%m-%d").replace(
                hour=9, tzinfo=datetime.now().astimezone().tzinfo,
            )
            run_at = local.isoformat()
            enabled = True
        service.create_task(
            title=row["text"],
            task_type="once",
            prompt=f"Reminder: {row['text']}",
            run_at=run_at,
            enabled=enabled,
            notes="Migrated from Memory Commitments.",
            cwd=cwd or os.getcwd(),
            authority=authority,
            extra={
                "legacy_commitment_id": row["id"],
                "legacy_source": row.get("source"),
            },
        )
        created += 1
    archive = legacy_archive_path()
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        source_bytes = source.read_bytes()
        if archive.read_bytes() == source_bytes:
            source.unlink()
        else:
            digest = hashlib.sha256(source_bytes).hexdigest()
            preserved = archive.with_name(f"commitments.{digest}.jsonl")
            if preserved.exists():
                if preserved.read_bytes() != source_bytes:
                    raise RuntimeError("legacy commitment archive collision")
                source.unlink()
            else:
                shutil.move(str(source), str(preserved))
                os.chmod(preserved, 0o600)
    else:
        shutil.move(str(source), str(archive))
        os.chmod(archive, 0o600)
    return created


__all__ = ["legacy_archive_path", "migrate_legacy_commitments"]
