"""Canonical execution identity, lifecycle, commands, and persistence.

This package owns the control-plane data model. Runtime-specific drivers live
beside their execution engines and may not write canonical lifecycle state
directly.
"""

from .model import (
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionEvent,
    ExecutionRecord,
    ExecutionStatus,
)
from .store import ExecutionStore, default_store

__all__ = [
    "CommandKind",
    "CommandStatus",
    "ControlCommand",
    "ExecutionEvent",
    "ExecutionRecord",
    "ExecutionStatus",
    "ExecutionStore",
    "default_store",
]
