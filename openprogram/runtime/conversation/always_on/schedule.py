"""When scheduled wakes may happen: quiet hours and wake days, in local time.

``quiet_hours`` (``{"start": "22:00", "end": "08:00"}`` or ``None``) is a
daily stretch without scheduled wakes; it may cross midnight. ``wake_days``
lists the weekdays (Monday is 0) wakes may happen on. Wake now and
messages from other conversations are not scheduled wakes and ignore both.

Design: docs/reference/design/runtime/always-on-agents.html
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def _quiet_at(moment: datetime, config: dict[str, Any]) -> bool:
    days = config.get("wake_days") or ALL_DAYS
    if moment.weekday() not in days:
        return True
    quiet = config.get("quiet_hours")
    if not quiet:
        return False
    start, end = _minutes(quiet["start"]), _minutes(quiet["end"])
    now = moment.hour * 60 + moment.minute
    return start <= now < end if start < end else (now >= start or now < end)


def is_quiet(at: float, config: dict[str, Any]) -> bool:
    """True when ``at`` falls in quiet hours or on a day without wakes."""
    return _quiet_at(datetime.fromtimestamp(at), config)


def next_allowed(at: float, config: dict[str, Any]) -> float:
    """``at`` itself, or the first moment after it outside quiet time.

    Quiet time only ends at a midnight or at the end of quiet hours, so the
    answer is ``at`` or one of those boundaries within the next eight days.
    """
    moment = datetime.fromtimestamp(at)
    if not _quiet_at(moment, config):
        return at
    quiet = config.get("quiet_hours")
    end = _minutes(quiet["end"]) if quiet else None
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = []
    for offset in range(9):
        day = midnight + timedelta(days=offset)
        candidates.append(day)
        if end is not None:
            candidates.append(day + timedelta(minutes=end))
    for candidate in sorted(candidates):
        if candidate > moment and not _quiet_at(candidate, config):
            return candidate.timestamp()
    return at  # unreachable with at least one wake day; never block the schedule
