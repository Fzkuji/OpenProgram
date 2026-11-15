"""The transcript's status words, decided once."""
from __future__ import annotations

_OPEN = {"pending", "running", "streaming", "paused", "cancelling"}
_KEPT = {"running", "streaming", "paused", "error", "cancelled", "interrupted"}


def stored_row_status(status: object, row_type: object = None) -> str:
    """A stored row's status as a read shows it.

    A row left cancelling has finished cancelling; older rows with no status
    take theirs from the row type.
    """
    if status in _KEPT:
        return str(status)
    if status == "cancelling":
        return "cancelled"
    return "error" if row_type == "error" else "done"


def is_open_status(status: object) -> bool:
    """Whether a stored row's status means it can still change on its own."""
    return status in _OPEN
