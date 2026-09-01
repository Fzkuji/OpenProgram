"""Canonical execution identity, lifecycle, commands, and persistence.

This package owns the control-plane data model. Runtime-specific drivers live
beside their execution engines and may not write canonical lifecycle state
directly.
"""

from .model import (
    CapabilitySet,
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionEvent,
    ExecutionRecord,
    ExecutionStatus,
    RevisionRecord,
    RunRecord,
)
from .attempts import AttemptRecord, AttemptStatus, AttemptStore
from .checkpoints import CheckpointManifest, ExecutionCheckpointStore
from .effects import (
    EffectClassification,
    EffectRecord,
    EffectStatus,
    EffectStore,
)
from .driver import (
    DriverAck,
    DriverBinding,
    DriverRegistry,
    ExecutionDriver,
    RuntimeSnapshot,
    TerminationReceipt,
)
from .store import ExecutionStore, default_store

__all__ = [
    "CommandKind",
    "CapabilitySet",
    "CommandStatus",
    "ControlCommand",
    "ExecutionEvent",
    "ExecutionRecord",
    "ExecutionStatus",
    "RevisionRecord",
    "RunRecord",
    "ExecutionStore",
    "AttemptRecord",
    "AttemptStatus",
    "AttemptStore",
    "CheckpointManifest",
    "ExecutionCheckpointStore",
    "EffectClassification",
    "EffectRecord",
    "EffectStatus",
    "EffectStore",
    "DriverAck",
    "DriverBinding",
    "DriverRegistry",
    "ExecutionDriver",
    "RuntimeSnapshot",
    "TerminationReceipt",
    "default_store",
]
