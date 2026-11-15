"""What each run of a scheduled task came to.

The worker process records every run that ended: ``quiet`` (nothing new),
``result`` (something for the user) or ``failed`` (the run broke, or it could
not do the check). A firing that found the previous run still going is
``skipped``, and a time that passed more than 12 hours before the worker could
answer it is ``missed``; several of one kind in a row share one row, so they do
not push real runs out of the last ``KEEP`` the task's page lists.

The record also counts failures in a row. One failure is often a passing
fault, so the user hears only when the count reaches ``FAILURE_NOTICE``, and
only that once; the next run that works clears the count without a message.
A skipped firing or a missed time says nothing about whether the task works
and leaves the count alone. A watch's check that failed counts too, until a
later check works; that later check takes back only the failed checks, since a
run that failed still counts until a run works.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any
from openprogram.host._platform.filesystem import replace_file

KEEP = 20
FAILURE_NOTICE = 3
QUIET, RESULT, FAILED, SKIPPED, MISSED = "quiet", "result", "failed", "skipped", "missed"
_TEXT_CHARS = 500


def _path() -> str:
    from openprogram.programs.tools.jobs.cron.cron import _resolve_path
    return os.path.join(os.path.dirname(_resolve_path()) or ".", "task-runs.json")


def _read(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: str, data: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix="task-runs.", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _failures_after(earlier: int, status: str) -> int:
    return earlier if status in (SKIPPED, MISSED) else earlier + 1 if status == FAILED else 0


def failures_after(task_id: str, status: str, execution_id: str | None = None) -> int | None:
    """The failures in a row a run would make; ``None`` if it is already on record."""
    entry = _read(_path()).get(task_id)
    entry = entry if isinstance(entry, dict) else {}
    if execution_id and any(run.get("execution_id") == execution_id for run in entry.get("runs") or []):
        return None
    return _failures_after(int(entry.get("failures") or 0), status)


def record(task_id: str, status: str, text: str = "", tip: str | None = None,
           now: float | None = None, execution_id: str | None = None, failed_check: bool = False) -> int | None:
    """Add one finished run; returns the failures in a row, this one included.

    A turn is one run however many paths report its end: with an
    ``execution_id`` already on record nothing changes and ``None`` comes back.
    """
    from openprogram.programs.tools.jobs.cron.cron import _store_lock

    path = _path()
    with _store_lock(path):
        data = _read(path)
        entry = data.get(task_id) if isinstance(data.get(task_id), dict) else {}
        if execution_id and any(run.get("execution_id") == execution_id for run in entry.get("runs") or []):
            return None
        earlier = int(entry.get("failures") or 0)
        failures = _failures_after(earlier, status)
        failed_checks = int(entry.get("failed_checks") or 0) + (status == FAILED and failed_check) if failures else 0
        run = {"at": time.time() if now is None else now, "status": status,
               "text": text[:_TEXT_CHARS], "tip": tip}
        if execution_id:
            run["execution_id"] = execution_id
        runs = list(entry.get("runs") or [])
        if status in (SKIPPED, MISSED):
            in_a_row = int(runs[0].get("count") or 1) if runs and runs[0].get("status") == status else 0
            run["count"] = in_a_row + 1
            if in_a_row:
                runs = runs[1:]
        data[task_id] = {"failures": failures, "failed_checks": failed_checks, "runs": [run, *runs][:KEEP]}
        _write(path, data)
    return failures


def clear_failed_checks(task_id: str) -> None:
    """A watch's check worked: the failed checks before it stop counting."""
    from openprogram.programs.tools.jobs.cron.cron import _store_lock

    path = _path()
    if not int((_read(path).get(task_id) or {}).get("failed_checks") or 0):
        return
    with _store_lock(path):
        data = _read(path)
        entry = data.get(task_id)
        if isinstance(entry, dict):
            entry["failures"] = max(int(entry.get("failures") or 0) - int(entry.get("failed_checks") or 0), 0)
            entry["failed_checks"] = 0
            _write(path, data)


def recent(task_id: str) -> list[dict[str, Any]]:
    """The task's kept runs, newest first."""
    entry = _read(_path()).get(task_id)
    return list(entry.get("runs") or []) if isinstance(entry, dict) else []


def failure_message(title: str, failures: int, reason: str) -> str:
    """The one message the user gets about a task that keeps failing."""
    if re.search(r"[一-鿿]", title + reason):
        return f"「{title}」连续 {failures} 次没有完成：{reason}"
    return f'"{title}" did not finish {failures} times in a row: {reason}'
