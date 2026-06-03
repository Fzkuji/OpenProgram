"""Worktree entity + state machine.

State transitions per ``docs/design/runtime/agent-worktree.md`` D3:

    create
       |
       v
    active --merge--> merged
       |
       +--discard--> discarded
       |
       +--keep--> kept
       |
       +--(merge fails)--> errored (back to active visually, but stamped
                                    error message — caller can retry or
                                    discard)

Terminal states are ``merged`` / ``discarded`` / ``kept`` / ``errored``
— absorbing. ``committing`` is a brief in-flight state used while a
``git merge`` is running, gating concurrent ops. The entity is plain
data; subprocess invocation lives in the manager.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class WorktreeStatus(str, Enum):
    ACTIVE = "active"
    COMMITTING = "committing"
    MERGED = "merged"
    DISCARDED = "discarded"
    KEPT = "kept"
    ERRORED = "errored"


TERMINAL_STATUSES: frozenset[WorktreeStatus] = frozenset({
    WorktreeStatus.MERGED,
    WorktreeStatus.DISCARDED,
    WorktreeStatus.KEPT,
})


# (from, to) pairs that are legal. Anything else raises ValueError in
# the manager so an LLM-driven state machine can't smuggle illegal
# transitions through the WS API.
_VALID_TRANSITIONS: frozenset[tuple[WorktreeStatus, WorktreeStatus]] = frozenset({
    (WorktreeStatus.ACTIVE, WorktreeStatus.COMMITTING),
    (WorktreeStatus.ACTIVE, WorktreeStatus.DISCARDED),
    (WorktreeStatus.ACTIVE, WorktreeStatus.KEPT),
    (WorktreeStatus.ACTIVE, WorktreeStatus.ERRORED),
    # If merge fails midway we step back to active so the caller can
    # retry / inspect. Important: don't auto-discard on conflict.
    (WorktreeStatus.COMMITTING, WorktreeStatus.MERGED),
    (WorktreeStatus.COMMITTING, WorktreeStatus.ACTIVE),
    (WorktreeStatus.COMMITTING, WorktreeStatus.ERRORED),
    (WorktreeStatus.ERRORED, WorktreeStatus.ACTIVE),  # caller cleared
    (WorktreeStatus.ERRORED, WorktreeStatus.DISCARDED),
    (WorktreeStatus.ERRORED, WorktreeStatus.KEPT),
})


def is_terminal(status: WorktreeStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(from_status: WorktreeStatus, to_status: WorktreeStatus) -> bool:
    return (from_status, to_status) in _VALID_TRANSITIONS


def mint_worktree_id() -> str:
    """Short-id with ``wt_`` prefix so the UI can disambiguate at a
    glance against task ids (``t_...``) and commit ids (plain hex)."""
    return "wt_" + uuid.uuid4().hex[:12]


@dataclass
class Worktree:
    """One ``git worktree`` instance the agent is using.

    Persisted to ``~/.openprogram/worktrees.json`` (see ``store.py``);
    serialises through ``to_dict`` / ``from_dict``.
    """

    id: str
    source_repo: str       # absolute path to user's real git repo
    worktree_path: str     # absolute path of the worktree on disk
    branch_name: str       # branch the worktree is checked out on
    base_ref: str = "HEAD"
    status: WorktreeStatus = WorktreeStatus.ACTIVE
    # Bookkeeping
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    # Optional linkage — both nullable. ``parent_session`` is the
    # OpenProgram session that owns this worktree; ``parent_task`` is
    # the async task (if any) currently bound to it. Multiple
    # worktrees may share a parent_session (plan-mode case); each
    # task is bound to at most one worktree.
    parent_session: Optional[str] = None
    parent_task: Optional[str] = None
    # Default merge mode the agent will use if it doesn't override.
    merge_strategy: str = "ff-only"
    # Outcome / audit
    merge_sha: Optional[str] = None
    files_changed: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Worktree":
        valid = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        known = {k: v for k, v in (d or {}).items() if k in valid}
        if "status" in known and not isinstance(known["status"], WorktreeStatus):
            try:
                known["status"] = WorktreeStatus(known["status"])
            except ValueError:
                known["status"] = WorktreeStatus.ACTIVE
        return cls(**known)


__all__ = [
    "Worktree",
    "WorktreeStatus",
    "TERMINAL_STATUSES",
    "is_terminal",
    "can_transition",
    "mint_worktree_id",
]
