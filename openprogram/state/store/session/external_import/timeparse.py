"""Small helpers both parsers share."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

TITLE_MAX = 80


def parse_timestamp(value: Any) -> Optional[float]:
    """Epoch seconds from an ISO-8601 string or an epoch number."""
    if isinstance(value, (int, float)):
        return float(value) / (1000 if value > 1e12 else 1)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fallback_title(first_user_text: str) -> str:
    # Credentials go before the cut: a key cut short no longer matches
    # its shape and would survive into the title.
    from .sanitize import remove_secret_values
    line = " ".join(remove_secret_values(first_user_text or "").split())
    if len(line) <= TITLE_MAX:
        return line
    return line[:TITLE_MAX - 1].rstrip() + "…"


_TAIL_BYTES = 256 * 1024


def last_message_time(path, kinds: tuple[str, ...]) -> Optional[float]:
    """Timestamp of the newest record of ``kinds``, read from the file's tail.

    File mtime is not the conversation's recency: both agents append
    bookkeeping records (prompt caches, latches) when a transcript is
    merely reopened.
    """
    import json
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if not any(f'"{k}"' in line for k in kinds):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("type") in kinds:
            ts = parse_timestamp(record.get("timestamp"))
            if ts:
                return ts
    return None
