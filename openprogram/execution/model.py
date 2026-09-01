"""Value objects for the canonical execution control plane."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Collection, Iterator
from typing import Any, Mapping


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.INTERRUPTED,
    }
)


class CommandKind(str, Enum):
    PAUSE = "execution.pause"
    CONTINUE = "execution.continue"
    STEP = "execution.step"
    STEER = "execution.steer"
    CANCEL = "execution.cancel"
    FORK = "execution.fork"
    RETRY = "execution.retry"


class CommandStatus(str, Enum):
    ACCEPTED = "accepted"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"


TERMINAL_COMMAND_STATUSES = frozenset({CommandStatus.APPLIED, CommandStatus.REJECTED})


def _dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_json(value: Any) -> Any:
    return json.loads(_json(value))


@dataclass(frozen=True)
class CapabilitySet:
    pause: bool = False
    step: bool = False
    steer: bool = False
    fork: bool = False
    retry: bool = False
    safe_point_kinds: tuple[str, ...] = ()
    state_schema_version: int | None = None

    def names(self) -> frozenset[str]:
        return frozenset(
            name
            for name in ("pause", "step", "steer", "fork", "retry")
            if getattr(self, name)
        )

    def __contains__(self, name: object) -> bool:
        return name in self.names()

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def __len__(self) -> int:
        return len(self.names())

    def to_dict(self) -> dict[str, Any]:
        return {
            "pause": self.pause,
            "step": self.step,
            "steer": self.steer,
            "fork": self.fork,
            "retry": self.retry,
            "safe_point_kinds": list(self.safe_point_kinds),
            "state_schema_version": self.state_schema_version,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any] | Collection[str]
    ) -> "CapabilitySet":
        if not isinstance(value, Mapping):
            names = frozenset(value)
            supported = frozenset({"pause", "step", "steer", "fork", "retry"})
            if not names.issubset(supported):
                raise ValueError("legacy capabilities contain an unsupported name")
            return cls(
                pause="pause" in names,
                step="step" in names,
                steer="steer" in names,
                fork="fork" in names,
                retry="retry" in names,
            )
        return cls(
            pause=bool(value.get("pause")),
            step=bool(value.get("step")),
            steer=bool(value.get("steer")),
            fork=bool(value.get("fork")),
            retry=bool(value.get("retry")),
            safe_point_kinds=tuple(value.get("safe_point_kinds") or ()),
            state_schema_version=(
                int(value["state_schema_version"])
                if value.get("state_schema_version") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    session_id: str
    created_at: float


@dataclass(frozen=True)
class RevisionRecord:
    revision_id: str
    content_hash: str
    manifest: Mapping[str, Any]
    created_at: float
    parent_revision_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", _snapshot_json(self.manifest))

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "content_hash": self.content_hash,
            "manifest": _snapshot_json(self.manifest),
            "created_at": self.created_at,
            "parent_revision_id": self.parent_revision_id,
        }


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    run_id: str
    session_id: str
    revision_id: str
    status: ExecutionStatus
    status_version: int
    parent_execution_id: str | None = None
    reason_code: str | None = None
    current_attempt_id: str | None = None
    owner_lease: Mapping[str, Any] = field(default_factory=dict)
    checkpoint_head_id: str | None = None
    safe_point: Mapping[str, Any] = field(default_factory=dict)
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    effect_summary: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    terminal_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "revision_id": self.revision_id,
            "status": self.status.value,
            "status_version": self.status_version,
            "parent_execution_id": self.parent_execution_id,
            "reason_code": self.reason_code,
            "current_attempt_id": self.current_attempt_id,
            "owner_lease": _dict(self.owner_lease),
            "checkpoint_head_id": self.checkpoint_head_id,
            "safe_point": _dict(self.safe_point),
            "capabilities": self.capabilities.to_dict(),
            "effect_summary": _dict(self.effect_summary),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal_at": self.terminal_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionRecord":
        return cls(
            execution_id=str(value["execution_id"]),
            run_id=str(value["run_id"]),
            session_id=str(value["session_id"]),
            revision_id=str(value["revision_id"]),
            status=ExecutionStatus(value["status"]),
            status_version=int(value["status_version"]),
            parent_execution_id=value.get("parent_execution_id") or None,
            reason_code=value.get("reason_code") or None,
            current_attempt_id=value.get("current_attempt_id") or None,
            owner_lease=_dict(value.get("owner_lease")),
            checkpoint_head_id=value.get("checkpoint_head_id") or None,
            safe_point=_dict(value.get("safe_point")),
            capabilities=CapabilitySet.from_dict(value.get("capabilities") or {}),
            effect_summary=_dict(value.get("effect_summary")),
            created_at=float(value.get("created_at") or 0.0),
            updated_at=float(value.get("updated_at") or 0.0),
            terminal_at=(
                float(value["terminal_at"])
                if value.get("terminal_at") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ControlCommand:
    command_id: str
    execution_id: str
    expected_version: int
    kind: CommandKind
    payload: Mapping[str, Any]
    actor: Mapping[str, Any]
    status: CommandStatus
    submitted_at: float
    updated_at: float
    result_version: int | None = None
    rejection_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "execution_id": self.execution_id,
            "expected_version": self.expected_version,
            "kind": self.kind.value,
            "payload": _dict(self.payload),
            "actor": _dict(self.actor),
            "status": self.status.value,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "result_version": self.result_version,
            "rejection_code": self.rejection_code,
        }


@dataclass(frozen=True)
class ExecutionEvent:
    sequence: int
    execution_id: str
    kind: str
    payload: Mapping[str, Any]
    created_at: float
    execution_version: int | None = None
    command_id: str | None = None
    schema_version: int = 1
