"""Job entity + state machine.

The entity is plain data — no runtime objects (cancel_event, future)
stored on it. Those live in the runner's parallel maps so the row
serialises cleanly to ``jobs.json`` and can be round-tripped through
crash recovery.

State transitions per ``docs/design/runtime/async-job-lifecycle.md`` D2:

  pending → queued → running → completed
                            ↘ cancelled
                            ↘ errored

  pending → cancelled / errored  (user stopped before pickup, or
                                  runner shutdown)
  queued  → cancelled / errored
  running → completed / cancelled / errored

Terminal states (``completed`` / ``cancelled`` / ``errored``) are
absorbing — no further transitions allowed.
"""
from __future__ import annotations

import time
import uuid
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Literal, Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERRORED = "errored"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset({
    JobStatus.COMPLETED,
    JobStatus.CANCELLED,
    JobStatus.ERRORED,
})


# (from, to) pairs that are legal. The runner / store rejects anything
# else with ValueError so a buggy code path can't drive a job from
# completed → running and lose audit history.
_VALID_TRANSITIONS: frozenset[tuple[JobStatus, JobStatus]] = frozenset({
    (JobStatus.PENDING, JobStatus.QUEUED),
    (JobStatus.PENDING, JobStatus.RUNNING),    # pool picked up immediately
    (JobStatus.PENDING, JobStatus.CANCELLED),
    (JobStatus.PENDING, JobStatus.ERRORED),
    (JobStatus.QUEUED, JobStatus.RUNNING),
    # Canonical executions no longer mirror their running transition into
    # JobStore. The projection may therefore advance directly from queued to
    # the canonical terminal outcome.
    (JobStatus.QUEUED, JobStatus.COMPLETED),
    (JobStatus.QUEUED, JobStatus.CANCELLED),
    (JobStatus.QUEUED, JobStatus.ERRORED),
    (JobStatus.RUNNING, JobStatus.COMPLETED),
    (JobStatus.RUNNING, JobStatus.CANCELLED),
    (JobStatus.RUNNING, JobStatus.ERRORED),
})


def is_terminal(status: JobStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(from_status: JobStatus, to_status: JobStatus) -> bool:
    return (from_status, to_status) in _VALID_TRANSITIONS


def mint_job_id() -> str:
    """16-hex-char id — short enough for UI display but with enough
    entropy to never collide within a session. Matches the existing
    msg_id style (12 hex chars from uuid4) with a ``j_`` prefix so
    the UI can disambiguate at a glance."""
    return "j_" + uuid.uuid4().hex[:14]


@dataclass
class Job:
    """One async unit of work. Serialises to ``jobs.json``."""

    id: str
    parent_session_id: str
    prompt: str
    agent_id: str
    # Optional human-readable bits — set by the caller. ``subject``
    # is used in the panel; ``description`` is the full prompt blob
    # (often duplicates ``prompt`` for backward compat).
    subject: str = ""
    description: str = ""
    # 'inherit' | 'clean'. inherit = fork off parent_msg_id; clean
    # = new root in the same session.
    context_mode: str = "inherit"
    parent_msg_id: Optional[str] = None
    parent_job_id: Optional[str] = None
    # The user msg in the caller's main chain that triggered this
    # spawn. Stays ON THE CALLER LANE regardless of context_mode
    # (parent_msg_id is None in clean mode, which loses the lane
    # info). The runner's auto-followup uses this to reset session
    # head back to the caller's lane before writing the follow-up
    # turn, so the follow-up commit sees the attach pointer in its
    # parent items rather than the sub-agent's own commit.
    caller_msg_id: Optional[str] = None
    # The session that INITIATED this job. Usually == parent_session_id
    # (the job runs in the caller's own session). For cross-session
    # messaging (send_message to another session) the job runs in the
    # TARGET session (parent_session_id) but its reply must be delivered
    # back to the INITIATOR's session — that's caller_session_id. None
    # means "same as parent_session_id" (the common case).
    caller_session_id: Optional[str] = None
    # Messages passed by this collaboration chain so far, for the loop
    # guard (send_message §5.1). The child turn this job runs is at
    # this count; further spawns/messages from it increment again.
    # 0 = a top-level (user-initiated) turn.
    chain_messages: int = 0
    # Generations of agents this chain has created, the other half of
    # the pair. The turn this job runs is at this count. A spawn sets
    # it to the dispatcher's count + 1; a dispatch to an EXISTING agent
    # (agent(to=…), send_message) creates nobody and passes it through.
    chain_generations: int = 0
    # The DISPATCHER's generation count, which the reply turn runs at
    # (JobRunner._dispatch_followup). Equal to chain_generations for a
    # dispatch, one less for a spawn. Reading a result must not cost a
    # generation: binding the finished child's count here is what left a
    # coordinator unable to create a second wave of agents after the
    # first wave reported back.
    caller_chain_generations: int = 0
    # agent(archive_when_done=True): archive the spawned branch when
    # the job reaches terminal state, after the result flowed back.
    # Set only by the agent tool's spawn form; deliveries to existing
    # branches leave it False.
    archive_when_done: bool = False
    # Exact execution options needed after durable queueing. Public sync
    # entry points used to pass these directly to run_agent_turn; keeping
    # them on Job lets the runner remain the only execution boundary.
    spawn_caller: Optional[str] = None
    advance_head: bool = False
    tools_override: Optional[list[str]] = None
    model_override: Optional[str] = None
    thinking_effort: Optional[str] = None
    render_range: Optional[dict[str, int]] = None
    # Durable intent for a busy-target delivery. Admission persists this
    # before inbox publication so a restarted runner can recreate a
    # missing entry without making the job dispatchable too early.
    deferred_inbox: Optional[dict[str, Any]] = None
    label: Optional[str] = None
    # Branch tip we *expect* this job to produce when it commits.
    # Filled in by the runner immediately so the UI can stitch
    # job_status → branch panel running animation. The actual
    # head_id (the persisted assistant_msg_id) is filled when the
    # turn lands.
    target_branch_head_id: Optional[str] = None
    # Set by _run_spawn / runner once it writes the placeholder
    # attach card. Lets the UI cross-reference job entity ↔ attach
    # card without an extra round-trip.
    attach_pointer_id: Optional[str] = None
    # Optional agent worktree this job is bound to. When set, the
    # job runner pre-binds ``_current_worktree_path`` ContextVar in
    # the worker thread so bash / edit / write / read use the worktree
    # as default cwd. Cancel hook (D15 in agent-worktree.md) may
    # auto-discard the worktree when the job is cancelled.
    worktree_id: Optional[str] = None
    # File-history ownership. Only same-session agent creation without a
    # worktree is owned by the origin turn; deliveries to existing peers,
    # cross-session work and isolated worktrees are never folded into it.
    creates_agent: bool = True
    relation: Literal["owned", "linked", "worktree"] = "owned"
    origin_turn_id: Optional[str] = None

    # True ⇒ the caller is blocking on this job (sync /task) — the
    # runner doesn't need to nudge anyone when it finishes, the
    # caller is already waiting. False ⇒ async — runner auto-dispatches
    # a follow-up LLM turn on the parent session so the agent that
    # spawned the job actually finds out it completed.
    wait: bool = True

    status: JobStatus = JobStatus.PENDING
    # Timestamps (float epoch seconds, None if not yet reached)
    created_at: float = field(default_factory=time.time)
    queued_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    cancel_requested_at: Optional[float] = None
    # Outcome
    head_id: Optional[str] = None
    result_text: Optional[str] = None
    error: Optional[str] = None
    attempt: int = 0
    # Appended so positional construction of the pre-authority Job schema
    # remains compatible.
    speaker_kind: Optional[str] = None
    speaker_id: Optional[str] = None
    speaker_display: Optional[str] = None
    principal_id: Optional[str] = None
    authority_tier: Optional[Literal["owner", "paired"]] = None
    interaction: Optional[str] = None
    # Resource-governance attribution is optional for backward compatibility.
    # Missing values identify jobs created before durable admission existed.
    admission_id: Optional[str] = None
    budget_scope_id: Optional[str] = None
    effective_limits: Optional[dict[str, Any]] = None
    resolved_limits_snapshot: Optional[dict[str, Any]] = None
    reason_code: Optional[str] = None
    # Frozen admission project binding used by public projections/auth.
    project_id: Optional[str] = None
    source: str = "agent_spawn"
    profile_snapshot: Optional[dict[str, Any]] = None
    response_format: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        # These execution inputs must survive jobs.json and must not retain
        # mutable containers owned by the submitting thread.
        for name in ("profile_snapshot", "response_format", "tools_override"):
            value = getattr(self, name)
            if value is not None:
                expected = list if name == "tools_override" else dict
                if not isinstance(value, expected):
                    raise ValueError(f"{name} must be a {expected.__name__}")
                setattr(self, name, json.loads(json.dumps(value, allow_nan=False)))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        # Defensive: strip unknown keys so future-version files don't
        # blow up on load.
        valid = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        known = {k: v for k, v in (d or {}).items() if k in valid}
        # Coerce status back to enum.
        if "status" in known:
            known["status"] = JobStatus(known["status"])
        return cls(**known)


__all__ = [
    "Job",
    "JobStatus",
    "TERMINAL_STATUSES",
    "is_terminal",
    "can_transition",
    "mint_job_id",
    "ExecutionSnapshot",
    "EventCursor",
    "JobResourceDTO",
]


def __getattr__(name: str):
    """Lazy aliases for the canonical public execution DTOs."""
    if name in {"ExecutionSnapshot", "EventCursor", "JobResourceDTO"}:
        from openprogram.execution.model import (
            EventCursor, ExecutionSnapshot, JobResourceDTO,
        )

        return {
            "ExecutionSnapshot": ExecutionSnapshot,
            "EventCursor": EventCursor,
            "JobResourceDTO": JobResourceDTO,
        }[name]
    raise AttributeError(name)
