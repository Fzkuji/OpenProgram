"""Value objects for the canonical execution control plane."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Collection, Iterator
from types import MappingProxyType
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
    WAIT_ANSWER = "execution.wait.answer"
    WAIT_DECLINE = "execution.wait.decline"


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


def _freeze_json(value: Any) -> Any:
    """Return a recursively immutable representation of JSON-like data."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Return ordinary JSON-compatible containers for persistence/serialization."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_thaw_json(item) for item in value]
        return sorted(items, key=lambda item: _json(item))
    return value


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
    source_checkpoint_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "revision_id": self.revision_id,
            "status": self.status.value,
            "status_version": self.status_version,
            "parent_execution_id": self.parent_execution_id,
            "source_checkpoint_id": self.source_checkpoint_id,
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
            source_checkpoint_id=value.get("source_checkpoint_id") or None,
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
    result_json: Mapping[str, Any] = field(default_factory=dict)

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
            "result_json": _dict(self.result_json),
        }


@dataclass(frozen=True)
class ExecutionEvent:
    """One canonical event.

    ``sequence`` is the SQLite event identity used by projection foreign
    keys.  ``execution_sequence`` is the public, contiguous cursor for this
    exact execution.  Keeping the two identities separate prevents events
    from unrelated executions from creating false reconnect gaps.
    """

    sequence: int
    execution_id: str
    kind: str
    payload: Mapping[str, Any]
    created_at: float
    execution_version: int | None = None
    command_id: str | None = None
    schema_version: int = 1
    execution_sequence: int = 0


@dataclass(frozen=True)
class EventReplay:
    """Authorized replay result for one execution cursor."""

    execution_id: str
    events: tuple[ExecutionEvent, ...]
    cursor: "EventCursor"
    recovery: str | None = None


@dataclass(frozen=True)
class AuditEvent:
    """Append-only, redacted audit record for an execution action."""

    audit_id: str
    sequence: int
    execution_id: str
    command_id: str | None
    draft_id: str | None
    wait_id: str | None
    correlation_id: str | None
    actor_binding: Mapping[str, Any]
    surface: str
    action: str
    policy_version: str
    project_binding: Mapping[str, Any]
    source_version: int | None
    checkpoint_id: str | None
    result: str
    reason_code: str | None
    redacted_payload: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "sequence": self.sequence,
            "execution_id": self.execution_id,
            "command_id": self.command_id,
            "draft_id": self.draft_id,
            "wait_id": self.wait_id,
            "correlation_id": self.correlation_id,
            "actor_binding": _snapshot_json(self.actor_binding),
            "surface": self.surface,
            "action": self.action,
            "policy_version": self.policy_version,
            "project_binding": _snapshot_json(self.project_binding),
            "source_version": self.source_version,
            "checkpoint_id": self.checkpoint_id,
            "result": self.result,
            "reason_code": self.reason_code,
            "redacted_payload": _snapshot_json(self.redacted_payload),
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ExecutionInputRecord:
    """Immutable input snapshot captured when an execution is admitted."""

    execution_id: str
    input_ref: str
    input_hash: str
    entrypoint: str
    session_id: str
    trusted_actor: Mapping[str, Any]
    config_snapshot_ref: str
    created_at: float
    user_message_id: str | None = None
    assistant_message_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "trusted_actor", _snapshot_json(self.trusted_actor))

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "input_ref": self.input_ref,
            "input_hash": self.input_hash,
            "entrypoint": self.entrypoint,
            "session_id": self.session_id,
            "user_message_id": self.user_message_id,
            "assistant_message_id": self.assistant_message_id,
            "trusted_actor": _snapshot_json(self.trusted_actor),
            "config_snapshot_ref": self.config_snapshot_ref,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class EventCursor:
    """Replay position for one canonical execution."""

    execution_id: str
    next_sequence: int
    snapshot_status_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "next_sequence": self.next_sequence,
            "snapshot_status_version": self.snapshot_status_version,
        }


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Complete public projection of an execution.

    ``ExecutionRecord`` remains the storage value.  This read model adds the
    project binding, resource view and event cursor required by every public
    transport without allowing transports to assemble partial snapshots.
    """

    execution_id: str
    job_id: str
    run_id: str
    parent_execution_id: str | None
    project_id: str
    session_id: str
    revision_id: str
    status: str
    status_version: int
    reason_code: str | None
    current_attempt_id: str | None
    owner_lease: Mapping[str, Any] | None
    resource: Mapping[str, Any] | None
    checkpoint_head_id: str | None
    safe_point: Mapping[str, Any] | None
    capabilities: Mapping[str, Any]
    pending_command_ids: tuple[str, ...]
    active_child_ids: tuple[str, ...]
    effect_summary: Mapping[str, Any]
    terminal_at: float | None
    updated_at: float
    event_sequence: int
    foreground_task: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "parent_execution_id": self.parent_execution_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "revision_id": self.revision_id,
            "status": self.status,
            "status_version": self.status_version,
            "reason_code": self.reason_code,
            "current_attempt_id": self.current_attempt_id,
            "owner_lease": _snapshot_json(self.owner_lease) if self.owner_lease else None,
            "resource": _snapshot_json(self.resource) if self.resource else None,
            "checkpoint_head_id": self.checkpoint_head_id,
            "safe_point": _snapshot_json(self.safe_point) if self.safe_point else None,
            "capabilities": _snapshot_json(self.capabilities),
            "pending_command_ids": list(self.pending_command_ids),
            "active_child_ids": list(self.active_child_ids),
            "effect_summary": _snapshot_json(self.effect_summary),
            "terminal_at": self.terminal_at,
            "updated_at": self.updated_at,
            "event_sequence": self.event_sequence,
            "foreground_task": _snapshot_json(self.foreground_task) if self.foreground_task else None,
        }


@dataclass(frozen=True)
class JobResourceDTO:
    """Canonical resource API returned by spawn/list/read surfaces."""

    job_id: str
    execution_id: str
    project_id: str
    session_id: str
    parent_execution_id: str | None
    label: str
    subject: str
    prompt_summary: str
    relation: str
    origin_turn_id: str | None
    status: str
    status_version: int
    capabilities: Mapping[str, Any]
    checkpoint_head_id: str | None
    resource: Mapping[str, Any] | None
    event_cursor: EventCursor
    execution: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = {
            "job_id": self.job_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "parent_execution_id": self.parent_execution_id,
            "label": self.label,
            "subject": self.subject,
            "prompt_summary": self.prompt_summary,
            "relation": self.relation,
            "origin_turn_id": self.origin_turn_id,
            "status": self.status,
            "status_version": self.status_version,
            "capabilities": _snapshot_json(self.capabilities),
            "checkpoint_head_id": self.checkpoint_head_id,
            "resource": _snapshot_json(self.resource) if self.resource else None,
            "event_cursor": self.event_cursor.to_dict(),
            "execution": _snapshot_json(self.execution),
        }
        return value
