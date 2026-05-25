"""Task entity + state machine.

The entity is plain data — no runtime objects (cancel_event, future)
stored on it. Those live in the runner's parallel maps so the row
serialises cleanly to ``tasks.json`` and can be round-tripped through
crash recovery.

State transitions per ``docs/design/async-task-lifecycle.md`` D2:

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
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERRORED = "errored"


TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
    TaskStatus.ERRORED,
})


# (from, to) pairs that are legal. The runner / store rejects anything
# else with ValueError so a buggy code path can't drive a task from
# completed → running and lose audit history.
_VALID_TRANSITIONS: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset({
    (TaskStatus.PENDING, TaskStatus.QUEUED),
    (TaskStatus.PENDING, TaskStatus.RUNNING),    # pool picked up immediately
    (TaskStatus.PENDING, TaskStatus.CANCELLED),
    (TaskStatus.PENDING, TaskStatus.ERRORED),
    (TaskStatus.QUEUED, TaskStatus.RUNNING),
    (TaskStatus.QUEUED, TaskStatus.CANCELLED),
    (TaskStatus.QUEUED, TaskStatus.ERRORED),
    (TaskStatus.RUNNING, TaskStatus.COMPLETED),
    (TaskStatus.RUNNING, TaskStatus.CANCELLED),
    (TaskStatus.RUNNING, TaskStatus.ERRORED),
})


def is_terminal(status: TaskStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    return (from_status, to_status) in _VALID_TRANSITIONS


def mint_task_id() -> str:
    """16-hex-char id — short enough for UI display but with enough
    entropy to never collide within a session. Matches the existing
    msg_id style (12 hex chars from uuid4) with a ``t_`` prefix so
    the UI can disambiguate at a glance."""
    return "t_" + uuid.uuid4().hex[:14]


@dataclass
class Task:
    """One async unit of work. Serialises to ``tasks.json``."""

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
    parent_task_id: Optional[str] = None
    # The user msg in the caller's main chain that triggered this
    # spawn. Stays ON THE CALLER LANE regardless of context_mode
    # (parent_msg_id is None in clean mode, which loses the lane
    # info). The runner's auto-followup uses this to reset session
    # head back to the caller's lane before writing the follow-up
    # turn, so the follow-up commit sees the attach pointer in its
    # parent items rather than the sub-agent's own commit.
    caller_msg_id: Optional[str] = None
    label: Optional[str] = None
    # Branch tip we *expect* this task to produce when it commits.
    # Filled in by the runner immediately so the UI can stitch
    # task_status → branch panel running animation. The actual
    # head_id (the persisted assistant_msg_id) is filled when the
    # turn lands.
    target_branch_head_id: Optional[str] = None
    # Set by _run_spawn / runner once it writes the placeholder
    # attach card. Lets the UI cross-reference task entity ↔ attach
    # card without an extra round-trip.
    attach_pointer_id: Optional[str] = None
    # Optional agent worktree this task is bound to. When set, the
    # task runner pre-binds ``_current_worktree_path`` ContextVar in
    # the worker thread so bash / edit / write / read use the worktree
    # as default cwd. Cancel hook (D15 in agent-worktree.md) may
    # auto-discard the worktree when the task is cancelled.
    worktree_id: Optional[str] = None

    # True ⇒ the caller is blocking on this task (sync /task) — the
    # runner doesn't need to nudge anyone when it finishes, the
    # caller is already waiting. False ⇒ async — runner auto-dispatches
    # a follow-up LLM turn on the parent session so the agent that
    # spawned the task actually finds out it completed.
    wait: bool = True

    status: TaskStatus = TaskStatus.PENDING
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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        # Defensive: strip unknown keys so future-version files don't
        # blow up on load.
        valid = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        known = {k: v for k, v in (d or {}).items() if k in valid}
        # Coerce status back to enum.
        if "status" in known:
            known["status"] = TaskStatus(known["status"])
        return cls(**known)


__all__ = [
    "Task",
    "TaskStatus",
    "TERMINAL_STATUSES",
    "is_terminal",
    "can_transition",
    "mint_task_id",
]
