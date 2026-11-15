"""Immutable execution checkpoints published at declared safe points."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .model import (
    ExecutionRecord,
    TERMINAL_EXECUTION_STATUSES,
    _freeze_json,
    _thaw_json,
)
from .store import ExecutionStore
from .model import _json


CHECKPOINT_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class CheckpointFragment:
    """Driver-owned state captured at one declared safe point."""

    safe_point_kind: str
    frontier: tuple[Mapping[str, Any], ...]
    state_refs: Mapping[str, Any]
    completed_frontier: tuple[Mapping[str, Any], ...] | None = None
    completed_actions: tuple[Mapping[str, Any], ...] = ()
    effect_receipts: tuple[Mapping[str, Any], ...] = ()
    child_frontier: Mapping[str, Any] = field(default_factory=dict)
    pending_command_ids: tuple[str, ...] = ()
    # A step safe point must report exactly one of these values.  They remain
    # driver data, rather than a second durable control-state store.
    managed_action: Mapping[str, Any] | None = None
    control_step: Mapping[str, Any] | None = None
    # An Agent turn leaves open steering for its next activation to deliver.
    leaves_steering_open: bool = False


@dataclass(frozen=True)
class CheckpointManifest:
    checkpoint_id: str
    execution_id: str
    revision_id: str
    parent_checkpoint_id: str | None
    source_execution_version: int
    frontier: tuple[Mapping[str, Any], ...]
    state_refs: Mapping[str, Any]
    completed_actions: tuple[Mapping[str, Any], ...]
    effect_receipts: tuple[Mapping[str, Any], ...]
    child_frontier: Mapping[str, Any]
    pending_command_ids: tuple[str, ...]
    created_by_attempt_id: str
    content_hash: str
    schema_version: int
    created_at: float
    completed_frontier: tuple[Mapping[str, Any], ...] | None = None

    @property
    def safe_point(self) -> Mapping[str, Any]:
        """The declared frontier boundary, never a live runtime object."""
        return _thaw_json(self.frontier[-1]) if self.frontier else {}

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frontier", tuple(_freeze_json(item) for item in self.frontier)
        )
        if self.completed_frontier is not None:
            object.__setattr__(
                self,
                "completed_frontier",
                tuple(_freeze_json(item) for item in self.completed_frontier),
            )
        object.__setattr__(self, "state_refs", _freeze_json(self.state_refs))
        object.__setattr__(
            self,
            "completed_actions",
            tuple(_freeze_json(item) for item in self.completed_actions),
        )
        object.__setattr__(
            self,
            "effect_receipts",
            tuple(_freeze_json(item) for item in self.effect_receipts),
        )
        object.__setattr__(self, "child_frontier", _freeze_json(self.child_frontier))

    def to_dict(self) -> dict[str, Any]:
        value = {
            "checkpoint_id": self.checkpoint_id,
            "execution_id": self.execution_id,
            "revision_id": self.revision_id,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "source_execution_version": self.source_execution_version,
            "frontier": _thaw_json(self.frontier),
            "completed_frontier": (
                _thaw_json(self.completed_frontier)
                if self.completed_frontier is not None
                else None
            ),
            "state_refs": _thaw_json(self.state_refs),
            "completed_actions": [
                _thaw_json(item) for item in self.completed_actions
            ],
            "effect_receipts": [_thaw_json(item) for item in self.effect_receipts],
            "child_frontier": _thaw_json(self.child_frontier),
            "pending_command_ids": list(self.pending_command_ids),
            "created_by_attempt_id": self.created_by_attempt_id,
            "content_hash": self.content_hash,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
        }
        # Mirror an Agent turn's routing fields for transports and diagnostics.
        state = _thaw_json(self.state_refs)
        agent_state = state.get("agent_position")
        if isinstance(agent_state, Mapping):
            state = agent_state
        for key in ("safe_point", "turn", "current_decision", "next_tool_index"):
            if key in state:
                value[key] = state[key]
        return value


class CheckpointConflict(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ExecutionCheckpointStore:
    def __init__(
        self,
        executions: ExecutionStore,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.executions = executions
        self._clock = clock

    def publish(
        self,
        execution_id: str,
        *,
        expected_version: int,
        revision_id: str,
        parent_checkpoint_id: str | None,
        frontier: Sequence[Mapping[str, Any]],
        state_refs: Mapping[str, Any],
        completed_actions: Sequence[Mapping[str, Any]],
        effect_receipts: Sequence[Mapping[str, Any]],
        child_frontier: Mapping[str, Any],
        pending_command_ids: Sequence[str],
        created_by_attempt_id: str,
        completed_frontier: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[CheckpointManifest, ExecutionRecord]:
        content = {
            "execution_id": execution_id,
            "revision_id": revision_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "source_execution_version": expected_version,
            "frontier": [dict(item) for item in frontier],
            "completed_frontier": (
                [dict(item) for item in completed_frontier]
                if completed_frontier is not None
                else None
            ),
            "state_refs": dict(state_refs),
            "completed_actions": [dict(item) for item in completed_actions],
            "effect_receipts": [dict(item) for item in effect_receipts],
            "child_frontier": dict(child_frontier),
            "pending_command_ids": list(pending_command_ids),
            "created_by_attempt_id": created_by_attempt_id,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
        }
        content_hash = hashlib.sha256(_json(content).encode("utf-8")).hexdigest()
        checkpoint_id = f"ckpt_{content_hash[:32]}"
        with self.executions._transaction() as connection:
            return self._publish_in_transaction(
                connection,
                execution_id=execution_id,
                expected_version=expected_version,
                revision_id=revision_id,
                parent_checkpoint_id=parent_checkpoint_id,
                frontier=frontier,
                completed_frontier=completed_frontier,
                state_refs=state_refs,
                completed_actions=completed_actions,
                effect_receipts=effect_receipts,
                child_frontier=child_frontier,
                pending_command_ids=pending_command_ids,
                created_by_attempt_id=created_by_attempt_id,
                checkpoint_id=checkpoint_id,
                content_hash=content_hash,
            )

    def _publish_in_transaction(
        self,
        connection,
        *,
        execution_id: str,
        expected_version: int,
        revision_id: str,
        parent_checkpoint_id: str | None,
        frontier: Sequence[Mapping[str, Any]],
        state_refs: Mapping[str, Any],
        completed_actions: Sequence[Mapping[str, Any]],
        effect_receipts: Sequence[Mapping[str, Any]],
        child_frontier: Mapping[str, Any],
        pending_command_ids: Sequence[str],
        created_by_attempt_id: str,
        completed_frontier: Sequence[Mapping[str, Any]] | None = None,
        checkpoint_id: str | None = None,
        content_hash: str | None = None,
        owner_lost: bool = False,
    ) -> tuple[CheckpointManifest, ExecutionRecord]:
        """Publish while an existing store transaction is held."""
        content = {
            "execution_id": execution_id,
            "revision_id": revision_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "source_execution_version": expected_version,
            "frontier": [dict(item) for item in frontier],
            "completed_frontier": (
                [dict(item) for item in completed_frontier]
                if completed_frontier is not None
                else None
            ),
            "state_refs": dict(state_refs),
            "completed_actions": [dict(item) for item in completed_actions],
            "effect_receipts": [dict(item) for item in effect_receipts],
            "child_frontier": dict(child_frontier),
            "pending_command_ids": list(pending_command_ids),
            "created_by_attempt_id": created_by_attempt_id,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
        }
        content_hash = content_hash or hashlib.sha256(_json(content).encode("utf-8")).hexdigest()
        checkpoint_id = checkpoint_id or f"ckpt_{content_hash[:32]}"
        existing = self._get(connection, checkpoint_id)
        if existing is not None:
            if existing.content_hash != content_hash:
                raise CheckpointConflict("id_collision", "checkpoint id names different content")
            execution = self.executions._require_execution(connection, execution_id)
            return existing, execution

        execution = self.executions._require_execution(connection, execution_id)
        if execution.revision_id != revision_id:
            raise CheckpointConflict("revision_mismatch", "checkpoint revision does not match execution revision")
        if execution.status_version != expected_version:
            raise CheckpointConflict("stale_version", f"expected execution version {expected_version}, found {execution.status_version}")
        if execution.status in TERMINAL_EXECUTION_STATUSES:
            raise CheckpointConflict("terminal", f"execution is already {execution.status.value}")
        now = self._clock()
        attempt = connection.get("attempts", created_by_attempt_id)
        if (
            attempt is None
            or attempt["execution_id"] != execution_id
            or execution.current_attempt_id != created_by_attempt_id
            or attempt["status"] != "active"
        ):
            raise CheckpointConflict("stale_attempt", "only the current active attempt can publish a checkpoint")
        unresolved = self.executions._first_unresolved_effect(connection, execution_id)
        if unresolved is not None:
            raise CheckpointConflict("unresolved_effect", f"effect requires resolution: {unresolved}")
        if execution.checkpoint_head_id != parent_checkpoint_id:
            raise CheckpointConflict("parent_mismatch", "parent_checkpoint_id is not the current checkpoint head")
        if parent_checkpoint_id is not None:
            parent = self._get(connection, parent_checkpoint_id)
            if parent is None or parent.execution_id != execution_id:
                raise CheckpointConflict("parent_not_found", "parent checkpoint does not belong to this execution")

        # References are execution-owned immutable blobs.  Validate all
        # schemas before writing either checkpoint row or head pointer so a
        # malformed Agent checkpoint cannot leave a half-published frontier.
        refs: set[str] = set()
        self.executions._collect_state_refs(dict(state_refs), refs)
        self.executions._collect_state_refs(list(effect_receipts), refs)
        self.executions._expand_state_blob_refs(connection, execution_id, refs)
        for ref in refs:
            row = connection.get("execution_state_blobs", execution_id, ref)
            if row is None:
                raise CheckpointConflict("state_ref_invalid", "checkpoint references a missing or foreign state blob")
            payload = bytes(row["payload"])
            if (
                hashlib.sha256(payload).hexdigest() != row["sha256"]
                or len(payload) != int(row["byte_length"])
                or not row["media_type"]
                or int(row["schema_version"]) < 1
            ):
                raise CheckpointConflict("state_blob_corrupt", "checkpoint state blob metadata is invalid")

        checkpoint = CheckpointManifest(
            checkpoint_id=checkpoint_id,
            execution_id=execution_id,
            revision_id=revision_id,
            parent_checkpoint_id=parent_checkpoint_id,
            source_execution_version=expected_version,
            frontier=tuple(dict(item) for item in frontier),
            completed_frontier=(
                tuple(dict(item) for item in completed_frontier)
                if completed_frontier is not None
                else None
            ),
            state_refs=dict(state_refs),
            completed_actions=tuple(dict(item) for item in completed_actions),
            effect_receipts=tuple(dict(item) for item in effect_receipts),
            child_frontier=dict(child_frontier),
            pending_command_ids=tuple(pending_command_ids),
            created_by_attempt_id=created_by_attempt_id,
            content_hash=content_hash,
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            created_at=now,
        )
        self._insert(connection, checkpoint)
        for ref in refs:
            connection.insert(
                "execution_state_blob_refs",
                {
                    "execution_id": execution_id,
                    "ref": ref,
                    "name": "checkpoint",
                    "reference_kind": "checkpoint",
                    "reference_id": checkpoint.checkpoint_id,
                    "created_at": now,
                },
                on_conflict="ignore",
            )
        safe_point = _thaw_json(checkpoint.frontier[-1]) if checkpoint.frontier else {}
        updated = connection.update(
            "executions",
            {
                "checkpoint_head_id": checkpoint_id,
                "safe_point_json": _json(safe_point),
                "status_version": expected_version + 1,
                "updated_at": now,
            },
            execution_id=execution_id,
            status_version=expected_version,
        )
        if updated != 1:
            raise CheckpointConflict("stale_version", "execution changed concurrently")
        execution = self.executions._require_execution(connection, execution_id)
        self.executions._append_event(connection, execution_id=execution_id, execution_version=execution.status_version, kind="execution.updated", payload={"record": execution.to_dict()}, created_at=now)
        self.executions._append_event(connection, execution_id=execution_id, execution_version=execution.status_version, kind="checkpoint.published", payload={"checkpoint": checkpoint.to_dict()}, created_at=now)
        return checkpoint, execution

    def get(self, checkpoint_id: str) -> CheckpointManifest | None:
        with closing(self.executions._connect()) as connection:
            return self._get(connection, checkpoint_id)

    def list_for_execution(self, execution_id: str) -> list[CheckpointManifest]:
        """Return published checkpoints for one execution in creation order."""
        with closing(self.executions._connect()) as connection:
            rows = connection.rows("checkpoints", order=("created_at", "checkpoint_id"), execution_id=execution_id)
            return [checkpoint for row in rows
                    if (checkpoint := self._get(connection, str(row["checkpoint_id"]))) is not None]

    @staticmethod
    def _insert(connection, checkpoint: CheckpointManifest) -> None:
        connection.insert(
            "checkpoints",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "execution_id": checkpoint.execution_id,
                "revision_id": checkpoint.revision_id,
                "parent_checkpoint_id": checkpoint.parent_checkpoint_id,
                "source_execution_version": checkpoint.source_execution_version,
                "frontier_json": _json(_thaw_json(checkpoint.frontier)),
                "state_refs_json": _json(_thaw_json(checkpoint.state_refs)),
                "completed_actions_json": _json(_thaw_json(checkpoint.completed_actions)),
                "completed_frontier_json": (
                    _json(_thaw_json(checkpoint.completed_frontier))
                    if checkpoint.completed_frontier is not None
                    else None
                ),
                "effect_receipts_json": _json(_thaw_json(checkpoint.effect_receipts)),
                "child_frontier_json": _json(_thaw_json(checkpoint.child_frontier)),
                "pending_commands_json": _json(checkpoint.pending_command_ids),
                "created_by_attempt_id": checkpoint.created_by_attempt_id,
                "content_hash": checkpoint.content_hash,
                "schema_version": checkpoint.schema_version,
                "created_at": checkpoint.created_at,
            },
        )

    @classmethod
    def _get(cls, connection, checkpoint_id: str) -> CheckpointManifest | None:
        row = connection.get("checkpoints", checkpoint_id)
        if row is None:
            return None
        return CheckpointManifest(
            checkpoint_id=str(row["checkpoint_id"]),
            execution_id=str(row["execution_id"]),
            revision_id=str(row["revision_id"]),
            parent_checkpoint_id=row["parent_checkpoint_id"],
            source_execution_version=int(row["source_execution_version"]),
            frontier=tuple(json.loads(row["frontier_json"])),
            completed_frontier=(
                tuple(json.loads(row["completed_frontier_json"]))
                if row["completed_frontier_json"] is not None
                else None
            ),
            state_refs=dict(json.loads(row["state_refs_json"])),
            completed_actions=tuple(json.loads(row["completed_actions_json"])),
            effect_receipts=tuple(json.loads(row["effect_receipts_json"])),
            child_frontier=dict(json.loads(row["child_frontier_json"])),
            pending_command_ids=tuple(json.loads(row["pending_commands_json"])),
            created_by_attempt_id=row["created_by_attempt_id"],
            content_hash=str(row["content_hash"]),
            schema_version=int(row["schema_version"]),
            created_at=float(row["created_at"]),
        )
