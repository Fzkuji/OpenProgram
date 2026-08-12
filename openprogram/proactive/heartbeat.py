"""Deterministic commitment reminder heartbeat."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time
import os
from pathlib import Path
import re
from typing import Any, Callable, Hashable, Iterator

from openprogram import _compat as fcntl
from openprogram.memory.management.transaction import workspace_write_lock
from openprogram.memory.runtime.commitments import (
    load_commitments,
    record_notification_steps,
)
from openprogram.memory.workspace_layout import ensure_runtime_dir


DEFAULT_QUIET_HOURS = "23:00-08:00"
DEFAULT_OVERDUE_INTERVAL_DAYS = 7
_CLOCK_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


@contextmanager
def _heartbeat_claim(memory_dir: Path) -> Iterator[None]:
    """Serialize reminder passes without holding the workspace write lock."""
    path = ensure_runtime_dir(memory_dir) / "heartbeat.lock"
    handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


def cadence_due(cadence: str, now: datetime) -> bool:
    cadence = str(cadence).strip().lower()
    if cadence == "hourly":
        return now.minute == 0
    if cadence == "daily":
        return now.hour == 9 and now.minute == 0
    return False


def _clock(value: str) -> time:
    if not isinstance(value, str) or not _CLOCK_RE.fullmatch(value.strip()):
        raise ValueError("quiet hours must use HH:MM-HH:MM")
    try:
        return time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("quiet hours must use HH:MM-HH:MM") from exc


def in_quiet_hours(now: datetime, quiet_hours: str) -> bool:
    try:
        start_raw, end_raw = str(quiet_hours).split("-", 1)
        start, end = _clock(start_raw), _clock(end_raw)
    except (AttributeError, ValueError) as exc:
        raise ValueError("quiet hours must use HH:MM-HH:MM") from exc
    current = now.time().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _notification_step(
    row: dict[str, Any],
    today: date,
    overdue_interval_days: int,
) -> str | None:
    if row.get("status") != "open" or not row.get("due"):
        return None
    due = date.fromisoformat(str(row["due"]))
    days = (today - due).days
    if days < 0:
        return None
    notified = set(row.get("notification_steps") or [])
    overdue = f"overdue:{overdue_interval_days}"
    passed_due = "due" in notified or any(
        step.startswith("overdue:") for step in notified
    )
    if not passed_due:
        return "due"
    if days >= overdue_interval_days and overdue not in notified:
        return overdue
    return None


def _render(rows: list[tuple[dict[str, Any], str]]) -> str:
    lines = ["Commitment reminder:"]
    for row, step in rows:
        label = "overdue" if step.startswith("overdue:") else "due"
        lines.append(f"- {row['text']} (due {row['due']}, {label})")
    return "\n".join(lines)


def target_for_source(source_id: str) -> tuple[str, str, str] | None:
    """Resolve an OpenProgram Source through its originating session."""
    parts = str(source_id).split("/", 2)
    if len(parts) != 3 or parts[0] != "openprogram":
        return None
    from openprogram.channels._question_bridge import (
        _channel_target_for_session,
    )

    return _channel_target_for_session(parts[1])


def run_heartbeat(
    memory_dir: str | Path,
    *,
    now: datetime,
    target_for_source: Callable[[str], Hashable | None],
    send: Callable[[Hashable, str], Any],
    quiet_hours: str = DEFAULT_QUIET_HOURS,
    overdue_interval_days: int = DEFAULT_OVERDUE_INTERVAL_DAYS,
) -> int:
    """Send due reminders and persist notification steps after success.

    The channel send is network I/O with its own retry budget, so it runs
    outside the workspace write lock; holding an exclusive lock across it would
    stall every other memory write for the length of a rate-limit backoff. The
    lock is taken to read the current rows and again to record the steps that
    were delivered, and recording merges only the step onto whatever the row
    says by then, so a status transition committed while a reminder was in
    flight survives.
    """
    if overdue_interval_days < 1:
        raise ValueError("overdue_interval_days must be positive")
    if in_quiet_hours(now, quiet_hours):
        return 0
    root = Path(memory_dir)
    with _heartbeat_claim(root):
        with workspace_write_lock(root):
            rows = load_commitments(root)
        grouped: dict[Hashable, list[tuple[dict[str, Any], str]]] = defaultdict(list)
        for row in rows:
            step = _notification_step(row, now.date(), overdue_interval_days)
            if step is None:
                continue
            target = target_for_source(str(row.get("source") or ""))
            if target is not None:
                grouped[target].append((row, step))
        delivered: dict[str, str] = {}
        for target, pending in grouped.items():
            if not bool(send(target, _render(pending))):
                continue
            for row, step in pending:
                delivered[str(row["id"])] = step
        if delivered:
            with workspace_write_lock(root):
                record_notification_steps(root, delivered)
        return len(delivered)


__all__ = [
    "DEFAULT_OVERDUE_INTERVAL_DAYS",
    "DEFAULT_QUIET_HOURS",
    "cadence_due",
    "in_quiet_hours",
    "run_heartbeat",
    "target_for_source",
]
