"""The maintenance gate: what the runtime asks before it starts work while
OpenProgram may be switching to an update.

Self-update (``openprogram.operations.self_update``) sits above the runtime:
it imports the runtime, and the runtime never imports it. The moments it
cares about belong to the runtime — a turn being admitted, a turn's reply
being committed, a Job being claimed, the worker restarting, the Job
runner's recovery pass, an unattended stage's turn being checked, a retry
being shown for approval — so the runtime owns this one port and asks it at
each of them. Self-update fills it in
``openprogram.operations.self_update.runtime_gate``, which the worker
(``operations/worker/runner.py``) and the Programs layer register when they
load; until something has, the first question loads the Programs through
``host.runtime_facts.load_programs``.

Without a registered gate each answer is what it is in a process where no
update can run: every turn and Job is admitted, nothing is held, a restart
blocks nothing, recovery only resumes the runtime's own interrupted runs.
The one exception is a turn that claims to be an update's own unattended
stage: it is refused, since only self-update can vouch for it.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from typing import Any, ContextManager

from openprogram.host import runtime_facts

#: The ``error_reason`` of a turn saved during a version switch; it runs once
#: the switch has finished, so its run has not ended.
HELD_REASON = "SELF_UPDATE_HELD"
#: What the sender of a held turn is told.
HELD_MESSAGE = "Switching versions; this runs as soon as that finishes."


class StateBusy(RuntimeError):
    """The update state is held by another process for a moment; the next
    pass does the work."""


HOOKS = frozenset({
    "turn_admission",
    "hold_turn",
    "turn_committed",
    "blocks",
    "open_state",
    "claim_job",
    "subscribe_changes",
    "job_permission_snapshot",
    "deliver_results",
    "reconcile_jobs",
    "require_execution",
    "approval_preview",
})

_hooks: dict[str, Callable[..., Any]] = {}
_load_attempted = False


def register(**hooks: Callable[..., Any]) -> None:
    """Install the gate's hook for each named moment."""
    unknown = set(hooks) - HOOKS
    if unknown:
        raise ValueError(f"unknown maintenance hooks: {sorted(unknown)}")
    _hooks.update(hooks)


def _hook(name: str) -> Callable[..., Any] | None:
    global _load_attempted
    if not _hooks and not _load_attempted:
        _load_attempted = True
        runtime_facts.load_programs()
    return _hooks.get(name)


def turn_admission(source: str) -> ContextManager[bool]:
    """Held around persisting a new turn; yields whether the turn may run.

    Entering maintenance waits for the context to close, so a turn that was
    admitted is one maintenance can see running.
    """
    hook = _hook("turn_admission")
    return hook(source) if hook else nullcontext(True)


def hold_turn(request: Any) -> bool:
    """Save a turn that was not admitted to run after the switch; True when
    it was saved (the caller answers with ``HELD_MESSAGE``). Called inside
    :func:`turn_admission`."""
    hook = _hook("hold_turn")
    return bool(hook(request)) if hook else False


def turn_committed(session_id: str, assistant_msg_id: str) -> None:
    """A turn's reply is durable: release an update that turn prepared."""
    hook = _hook("turn_committed")
    if hook:
        hook(session_id, assistant_msg_id)


def blocks(reason: str) -> bool:
    """Whether maintenance keeps the work named by ``reason`` (``"worker_restart"``)
    from starting now."""
    hook = _hook("blocks")
    return bool(hook(reason)) if hook else False


def open_state() -> Any:
    """The update state a Job runner reads for its whole life, opened on the
    profile it started on; None without a gate."""
    hook = _hook("open_state")
    return hook() if hook else None


def claim_job(governor: Any, *, state: Any, owner_instance_id: str,
              excluded_sessions: set[str], only_job_id: str | None) -> Any:
    """Claim the next queued Job, serialized with entering maintenance; while
    maintenance is on, only the update's own stage may be claimed."""
    hook = _hook("claim_job")
    if hook:
        return hook(governor, state=state, owner_instance_id=owner_instance_id,
                    excluded_sessions=excluded_sessions, only_job_id=only_job_id)
    return governor.claim_next(owner_instance_id=owner_instance_id,
                               excluded_sessions=excluded_sessions, only_job_id=only_job_id)


def subscribe_changes(state: Any, on_change: Callable[[tuple[str, ...]], None]) -> Any:
    """Call ``on_change`` when the update state changes; returns the
    subscription (None without a gate)."""
    hook = _hook("subscribe_changes")
    return hook(state, on_change) if hook else None


def job_permission_snapshot(execution_store: Any, job: Any, current: Any) -> Any:
    """The permissions an update's own Job runs under; ``current`` for any
    other Job."""
    hook = _hook("job_permission_snapshot")
    return hook(execution_store, job, current) if hook else current


def deliver_results() -> None:
    """Deliver finished updates' results to their conversations. Raises
    :class:`StateBusy` when the state is held elsewhere for a moment."""
    hook = _hook("deliver_results")
    if hook:
        hook()


def reconcile_jobs(runner: Any, resume_interrupted: Callable[[Any], None]) -> None:
    """The Job runner's recovery pass: the gate settles its own restarts and
    stages around ``resume_interrupted``, which resumes the runs a restart
    paused. Raises :class:`StateBusy` like :func:`deliver_results`."""
    hook = _hook("reconcile_jobs")
    if hook:
        hook(runner, resume_interrupted)
    else:
        resume_interrupted(runner)


def require_execution(source: str, **fields: Any) -> None:
    """Refuse a turn whose ``source`` names an update's unattended stage
    unless it runs inside that stage's own Job, with the frozen inputs in
    ``fields`` (``request`` for a continuation)."""
    hook = _hook("require_execution")
    if hook is None:
        raise RuntimeError(f"{source} turns run only where self-update is loaded")
    hook(source, **fields)


def approval_preview(update_id: Any, candidate_sha: Any, request: Any) -> dict:
    """What an approval card shows for retrying an update with a candidate."""
    hook = _hook("approval_preview")
    if hook is None:
        raise RuntimeError("self-update is not loaded")
    return hook(update_id, candidate_sha, request)


__all__ = [
    "HELD_MESSAGE",
    "HELD_REASON",
    "HOOKS",
    "StateBusy",
    "approval_preview",
    "blocks",
    "claim_job",
    "deliver_results",
    "hold_turn",
    "job_permission_snapshot",
    "open_state",
    "reconcile_jobs",
    "register",
    "require_execution",
    "subscribe_changes",
    "turn_admission",
    "turn_committed",
]
