"""Exceptions raised by execution cancellation and child admission."""
from __future__ import annotations

from typing import Any


class ExecutionNotFound(Exception):
    """The requested execution is absent or not visible to the caller."""


class ExecutionNotCancellable(Exception):
    """The requested execution has already reached a non-cancel terminal."""

    def __init__(self, execution_id: str, execution: Any = None) -> None:
        super().__init__(execution_id)
        self.execution_id = execution_id
        self.execution = execution


class ExecutionSpawnRefused(Exception):
    """Creating a descendant was refused because an ancestor is cancelling."""

    def __init__(self, reason_code: str = "cancel.parent") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
