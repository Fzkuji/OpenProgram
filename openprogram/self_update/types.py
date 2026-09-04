"""Durable data contract for conversational self-update."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
import math
import os
import re
import time
from typing import Any, Mapping
import uuid


SCHEMA_VERSION = 1
DEFAULT_APP_PATH = "/Applications/OpenProgram.app"
_UPDATE_ID = re.compile(r"^su_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class SelfUpdateError(RuntimeError):
    """Base error for self-update state operations."""


class ActiveUpdateError(SelfUpdateError):
    """Another non-terminal update already owns the active slot."""


class UpdateExistsError(SelfUpdateError):
    """The requested update id already exists."""


class UpdateNotFoundError(SelfUpdateError):
    """No durable record exists for an update id."""


class CorruptUpdateStateError(SelfUpdateError):
    """Persisted state is malformed or uses an unsupported schema."""


class InvalidTransitionError(SelfUpdateError):
    """The requested state-machine edge is not legal."""


class ConcurrentUpdateError(SelfUpdateError):
    """The caller's expected state no longer matches durable state."""


class UpdatePhase(str, Enum):
    PREPARING = "preparing"
    STAGING = "staging"
    READY = "ready"
    ACTIVATING = "activating"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    ABORTED = "aborted"
    ROLLED_BACK = "rolled_back"
    NEEDS_MANUAL_RECOVERY = "needs_manual_recovery"


class IterationMode(str, Enum):
    APPROVE_EACH_ACTIVATION = "approve_each_activation"
    BOUNDED_AUTO = "bounded_auto"


TERMINAL_PHASES = frozenset(
    {
        UpdatePhase.SUCCEEDED,
        UpdatePhase.ABORTED,
        UpdatePhase.ROLLED_BACK,
        UpdatePhase.NEEDS_MANUAL_RECOVERY,
    }
)

VALID_TRANSITIONS = frozenset(
    {
        (UpdatePhase.PREPARING, UpdatePhase.STAGING),
        (UpdatePhase.PREPARING, UpdatePhase.ABORTED),
        (UpdatePhase.STAGING, UpdatePhase.READY),
        (UpdatePhase.STAGING, UpdatePhase.ABORTED),
        (UpdatePhase.READY, UpdatePhase.ACTIVATING),
        (UpdatePhase.READY, UpdatePhase.ABORTED),
        (UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING),
        (UpdatePhase.ACTIVATING, UpdatePhase.ROLLED_BACK),
        (UpdatePhase.ACTIVATING, UpdatePhase.NEEDS_MANUAL_RECOVERY),
        (UpdatePhase.VERIFYING, UpdatePhase.SUCCEEDED),
        (UpdatePhase.VERIFYING, UpdatePhase.ROLLED_BACK),
        (UpdatePhase.VERIFYING, UpdatePhase.NEEDS_MANUAL_RECOVERY),
    }
)


def is_terminal(phase: UpdatePhase) -> bool:
    return phase in TERMINAL_PHASES


def can_transition(source: UpdatePhase, target: UpdatePhase) -> bool:
    return (source, target) in VALID_TRANSITIONS


def mint_update_id() -> str:
    return "su_" + uuid.uuid4().hex


def _required_text(value: str, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValueError(f"{name} is required and must be at most {maximum} characters")
    return value


def _validate_update_id(value: str) -> str:
    if not isinstance(value, str) or not _UPDATE_ID.fullmatch(value):
        raise ValueError("update_id must match su_[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    return value


def _validate_changed_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("changed path must be a string")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or str(path) != value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(
            f"changed path must be a normalized relative POSIX path: {value!r}"
        )
    return value


@dataclass(frozen=True)
class IterationPolicy:
    mode: IterationMode = IterationMode.APPROVE_EACH_ACTIVATION
    max_attempts: int = 3
    deadline: float | None = None
    allowed_paths: tuple[str, ...] = ()
    required_tests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, IterationMode):
            raise ValueError("iteration mode must be an IterationMode")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= 3
        ):
            raise ValueError("max_attempts must be between 1 and 3")
        if self.deadline is not None and (
            isinstance(self.deadline, bool)
            or not isinstance(self.deadline, (int, float))
            or not math.isfinite(self.deadline)
            or self.deadline < 0
        ):
            raise ValueError("deadline must be a finite non-negative timestamp or None")
        if not isinstance(self.allowed_paths, tuple) or not isinstance(
            self.required_tests, tuple
        ):
            raise ValueError("allowed_paths and required_tests must be tuples")
        for path in self.allowed_paths:
            _validate_changed_path(path)
        for command in self.required_tests:
            _required_text(command, "required test")
        if self.mode is IterationMode.BOUNDED_AUTO and not self.allowed_paths:
            raise ValueError("bounded_auto requires allowed_paths")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "max_attempts": self.max_attempts,
            "deadline": self.deadline,
            "allowed_paths": list(self.allowed_paths),
            "required_tests": list(self.required_tests),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IterationPolicy":
        if set(data) != {
            "mode",
            "max_attempts",
            "deadline",
            "allowed_paths",
            "required_tests",
        }:
            raise CorruptUpdateStateError("malformed iteration policy")
        if not isinstance(data.get("allowed_paths"), list) or not isinstance(
            data.get("required_tests"), list
        ):
            raise CorruptUpdateStateError("iteration paths and tests must be arrays")
        try:
            return cls(
                mode=IterationMode(data["mode"]),
                max_attempts=data["max_attempts"],
                deadline=data["deadline"],
                allowed_paths=tuple(data["allowed_paths"]),
                required_tests=tuple(data["required_tests"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptUpdateStateError("malformed iteration policy") from exc


@dataclass(frozen=True)
class UpdateRequest:
    update_id: str
    session_id: str
    origin_turn_id: str
    origin_assistant_id: str
    agent_id: str
    repo: str
    worktree_id: str
    base_sha: str
    candidate_sha: str
    changed_paths: tuple[str, ...]
    pre_update_evidence: tuple[str, ...]
    goal: str
    assertions: tuple[str, ...]
    iteration_policy: IterationPolicy = field(default_factory=IterationPolicy)
    created_at: float = field(default_factory=time.time)
    timeout_seconds: int = 1800
    app_path: str = DEFAULT_APP_PATH

    def __post_init__(self) -> None:
        _validate_update_id(self.update_id)
        for name in (
            "session_id",
            "origin_turn_id",
            "origin_assistant_id",
            "agent_id",
            "worktree_id",
        ):
            _required_text(getattr(self, name), name, maximum=256)
        if (
            not isinstance(self.repo, str)
            or not Path(self.repo).is_absolute()
            or os.path.normpath(self.repo) != self.repo
        ):
            raise ValueError("repo must be a normalized absolute path")
        if (
            isinstance(self.created_at, bool)
            or not isinstance(self.created_at, (int, float))
            or not math.isfinite(self.created_at)
            or self.created_at < 0
        ):
            raise ValueError("created_at must be a finite non-negative timestamp")
        if (
            not isinstance(self.base_sha, str)
            or not isinstance(self.candidate_sha, str)
            or not _GIT_SHA.fullmatch(self.base_sha)
            or not _GIT_SHA.fullmatch(self.candidate_sha)
        ):
            raise ValueError(
                "base_sha and candidate_sha must be 40 lowercase hex characters"
            )
        if self.base_sha == self.candidate_sha:
            raise ValueError("candidate_sha must differ from base_sha")
        if not isinstance(self.changed_paths, tuple) or not self.changed_paths:
            raise ValueError("changed_paths must not be empty")
        for path in self.changed_paths:
            _validate_changed_path(path)
        if (
            not isinstance(self.pre_update_evidence, tuple)
            or not self.pre_update_evidence
        ):
            raise ValueError("pre_update_evidence must not be empty")
        for evidence in self.pre_update_evidence:
            _required_text(evidence, "pre-update evidence")
        _required_text(self.goal, "goal")
        if not isinstance(self.assertions, tuple) or not self.assertions:
            raise ValueError("assertions must not be empty")
        for assertion in self.assertions:
            _required_text(assertion, "assertion")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= 86400
        ):
            raise ValueError("timeout_seconds must be between 1 and 86400")
        if self.app_path != DEFAULT_APP_PATH:
            raise ValueError(f"app_path must be {DEFAULT_APP_PATH}")
        if not isinstance(self.iteration_policy, IterationPolicy):
            raise ValueError("iteration_policy must be an IterationPolicy")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "update_id": self.update_id,
            "created_at": self.created_at,
            "session_id": self.session_id,
            "origin_turn_id": self.origin_turn_id,
            "origin_assistant_id": self.origin_assistant_id,
            "agent_id": self.agent_id,
            "repo": self.repo,
            "worktree_id": self.worktree_id,
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "changed_paths": list(self.changed_paths),
            "pre_update_evidence": list(self.pre_update_evidence),
            "acceptance": {
                "goal": self.goal,
                "assertions": list(self.assertions),
            },
            "timeout_seconds": self.timeout_seconds,
            "app_path": self.app_path,
            "iteration_policy": self.iteration_policy.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UpdateRequest":
        expected = {
            "schema",
            "update_id",
            "created_at",
            "session_id",
            "origin_turn_id",
            "origin_assistant_id",
            "agent_id",
            "repo",
            "worktree_id",
            "base_sha",
            "candidate_sha",
            "changed_paths",
            "pre_update_evidence",
            "acceptance",
            "timeout_seconds",
            "app_path",
            "iteration_policy",
        }
        if set(data) != expected or data.get("schema") != SCHEMA_VERSION:
            raise CorruptUpdateStateError("unsupported or malformed request schema")
        acceptance = data.get("acceptance")
        if not isinstance(acceptance, Mapping) or set(acceptance) != {
            "goal",
            "assertions",
        }:
            raise CorruptUpdateStateError("malformed acceptance contract")
        if (
            not isinstance(data.get("changed_paths"), list)
            or not isinstance(data.get("pre_update_evidence"), list)
            or not isinstance(acceptance.get("assertions"), list)
            or not isinstance(data.get("iteration_policy"), Mapping)
        ):
            raise CorruptUpdateStateError(
                "paths, evidence, assertions, and policy are malformed"
            )
        if (
            isinstance(data.get("created_at"), bool)
            or not isinstance(data.get("created_at"), (int, float))
            or isinstance(data.get("timeout_seconds"), bool)
            or not isinstance(data.get("timeout_seconds"), int)
        ):
            raise CorruptUpdateStateError("malformed request counters")
        try:
            return cls(
                update_id=data["update_id"],
                created_at=float(data["created_at"]),
                session_id=data["session_id"],
                origin_turn_id=data["origin_turn_id"],
                origin_assistant_id=data["origin_assistant_id"],
                agent_id=data["agent_id"],
                repo=data["repo"],
                worktree_id=data["worktree_id"],
                base_sha=data["base_sha"],
                candidate_sha=data["candidate_sha"],
                changed_paths=tuple(data["changed_paths"]),
                pre_update_evidence=tuple(data["pre_update_evidence"]),
                goal=acceptance["goal"],
                assertions=tuple(acceptance["assertions"]),
                iteration_policy=IterationPolicy.from_dict(data["iteration_policy"]),
                timeout_seconds=int(data["timeout_seconds"]),
                app_path=data["app_path"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptUpdateStateError("malformed request data") from exc


@dataclass(frozen=True)
class VerifierDispatch:
    job_id: str
    claimed_by: str
    lease_until: float
    generation: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "claimed_by": self.claimed_by,
            "lease_until": self.lease_until,
            "generation": self.generation,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VerifierDispatch":
        if set(data) != {"job_id", "claimed_by", "lease_until", "generation"}:
            raise CorruptUpdateStateError("malformed verifier dispatch")
        try:
            if (
                isinstance(data["lease_until"], bool)
                or not isinstance(data["lease_until"], (int, float))
                or isinstance(data["generation"], bool)
                or not isinstance(data["generation"], int)
            ):
                raise ValueError("invalid verifier dispatch types")
            dispatch = cls(
                job_id=_required_text(data["job_id"], "job_id", maximum=256),
                claimed_by=_required_text(
                    data["claimed_by"], "claimed_by", maximum=256
                ),
                lease_until=float(data["lease_until"]),
                generation=int(data["generation"]),
            )
            if (
                dispatch.generation < 1
                or not math.isfinite(dispatch.lease_until)
                or dispatch.lease_until < 0
            ):
                raise ValueError("invalid verifier dispatch counters")
            return dispatch
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptUpdateStateError("malformed verifier dispatch") from exc


@dataclass(frozen=True)
class UpdateState:
    update_id: str
    phase: UpdatePhase
    revision: int
    updated_at: float
    attempt: int = 1
    dispatch: VerifierDispatch | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)
    last_event: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "update_id": self.update_id,
            "phase": self.phase.value,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "attempt": self.attempt,
            "dispatch": self.dispatch.to_dict() if self.dispatch else None,
            "detail": dict(self.detail),
            "last_event": dict(self.last_event),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UpdateState":
        expected = {
            "schema",
            "update_id",
            "phase",
            "revision",
            "updated_at",
            "attempt",
            "dispatch",
            "detail",
            "last_event",
        }
        if set(data) != expected or data.get("schema") != SCHEMA_VERSION:
            raise CorruptUpdateStateError("unsupported or malformed state schema")
        try:
            dispatch = data["dispatch"]
            if dispatch is not None and not isinstance(dispatch, Mapping):
                raise ValueError("dispatch must be an object or null")
            if not isinstance(data["detail"], Mapping):
                raise ValueError("detail must be an object")
            if not isinstance(data["last_event"], Mapping):
                raise ValueError("last_event must be an object")
            if (
                isinstance(data["revision"], bool)
                or not isinstance(data["revision"], int)
                or isinstance(data["attempt"], bool)
                or not isinstance(data["attempt"], int)
                or isinstance(data["updated_at"], bool)
                or not isinstance(data["updated_at"], (int, float))
            ):
                raise ValueError("invalid state field types")
            state = cls(
                update_id=_validate_update_id(data["update_id"]),
                phase=UpdatePhase(data["phase"]),
                revision=int(data["revision"]),
                updated_at=float(data["updated_at"]),
                attempt=int(data["attempt"]),
                dispatch=(
                    VerifierDispatch.from_dict(dispatch)
                    if isinstance(dispatch, Mapping)
                    else None
                ),
                detail=dict(data["detail"]),
                last_event=dict(data["last_event"]),
            )
            if (
                state.revision < 1
                or state.attempt < 1
                or not math.isfinite(state.updated_at)
                or state.updated_at < 0
            ):
                raise ValueError("invalid state counters")
            return state
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptUpdateStateError("malformed state data") from exc


@dataclass(frozen=True)
class UpdateRecord:
    request: UpdateRequest
    state: UpdateState


@dataclass(frozen=True)
class VerifierClaim:
    acquired: bool
    job_id: str
    generation: int
    lease_until: float
