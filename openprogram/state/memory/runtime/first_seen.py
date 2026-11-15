"""The day memory first read each paragraph that cites nothing.

A paragraph with a citation carries its own date, the day the cited
conversation happened. One without (a person's note, a file another tool
wrote) has none, and Timeline still files it, under the day memory first
recorded it. That day lives in ``<runtime-dir>/first-seen.json``, keyed by
paragraph ID (its ``^id`` or its derived ID), as ``YYYY-MM-DD``.

The day is ours to record, not the file's: a modification time moves
whenever a file is copied, synced or reformatted, so the same sentence
would change days for no reason. The first time a file's paragraphs are
seen, the day the file entered memory's Git history stands in, when it
has one; otherwise it is today. A reworded paragraph is a new paragraph
(its derived ID changes with its text) and gets a new day.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from ..workspace_layout import ensure_runtime_dir

logger = logging.getLogger(__name__)

FIRST_SEEN_FILE = "first-seen.json"


def _path(memory_dir: Path) -> Path:
    return ensure_runtime_dir(memory_dir) / FIRST_SEEN_FILE


def load(memory_dir: Path) -> dict[str, str]:
    try:
        payload = json.loads(_path(Path(memory_dir)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = payload.get("days") if isinstance(payload, dict) else None
    return {
        str(key): value for key, value in (days or {}).items()
        if isinstance(value, str)
    }


def _added_on(memory_dir: Path, topic_path: str) -> str | None:
    """The day ``topics/<topic_path>`` first entered memory's history."""
    if not (memory_dir / ".git").exists():
        return None
    try:
        output = subprocess.run(
            ["git", "--literal-pathspecs", "log", "--diff-filter=A",
             # Git reads pathspecs with forward slashes on Windows too.
             "--format=%cs", "--", "topics/" + str(topic_path).replace("\\", "/")],
            cwd=memory_dir, capture_output=True, text=True, encoding="utf-8", timeout=15,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    days = [line.strip() for line in output.splitlines() if line.strip()]
    # Newest first: the last is the first time the file was added.
    return days[-1] if days else None


def dates(memory_dir: Path, units: Iterable[Any]) -> dict[str, str]:
    """The recorded day of every uncited paragraph in ``units``, recording
    the ones seen for the first time. Never raises."""
    memory_dir = Path(memory_dir)
    try:
        known = load(memory_dir)
        missing: dict[str, list[str]] = {}
        for unit in units:
            if unit.source_refs or unit.memory_id in known:
                continue
            missing.setdefault(unit.topic_path, []).append(unit.memory_id)
        if not missing:
            return known
        today = date.today().isoformat()
        for topic_path, ids in sorted(missing.items()):
            # One Git call per file, only for files with new paragraphs.
            day = _added_on(memory_dir, topic_path) or today
            for memory_id in ids:
                known[memory_id] = day
        from openprogram.state.store.session.atomic_file import atomic_write_text

        atomic_write_text(
            _path(memory_dir),
            json.dumps({"version": 1, "days": dict(sorted(known.items()))},
                       indent=2, ensure_ascii=False),
        )
        return known
    except Exception as exc:  # noqa: BLE001 - a view never fails a write
        logger.debug("memory: first-seen days not recorded: %s", exc)
        return {}


__all__ = ["FIRST_SEEN_FILE", "dates", "load"]
