"""What Activity shows for one agent: its status and what its work cost.

One status set for every agent row (the conversation's own run, a helper,
a message sent to another conversation), the one Claude Code's footer
badges and Oh My Pi's Agent Hub use:

- ``running``: working.
- ``needs approval``: stopped at a permission card.
- ``needs input``: stopped at a question it asked.
- ``stalled``: still running but quiet past ``agent.stall_minutes``; its
  caller was told once (``delegation_watch``).
- ``idle``: finished, or stopped by the person; it can take more work.
- ``failed``: ended in an error, or a limit (runtime, spend) stopped it.

Cost and tokens are the agent's subtree: its own model calls and those of
every helper it started (``agent_result.subtree_job_ids``), the same total
the result block reports.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html, "Activity".
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any, Callable, Iterable, Optional

from openprogram.runtime.jobs.types import Job, JobStatus, is_terminal

_log = logging.getLogger(__name__)

STATUSES = ("running", "needs approval", "needs input", "stalled", "idle", "failed")

# A helper that ended, with every helper under it ended too, never changes:
# the roster reads its usage once.
_SETTLED_ENTRIES = 512
_settled: "OrderedDict[tuple[str, Optional[float]], dict[str, Any]]" = OrderedDict()
_settled_lock = threading.Lock()


def _open_wait_kind(job_id: str) -> Optional[str]:
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.waits import DurableWaitStore
        waits = DurableWaitStore(default_store()).list_open(execution_id=job_id)
    except Exception:  # noqa: BLE001 - no execution store: nothing is asked
        return None
    kinds = [wait.kind for wait in waits]
    if "approval" in kinds:
        return "approval"
    return kinds[0] if kinds else None


def _was_stalled(job: Job) -> bool:
    try:
        from openprogram.runtime.jobs import notices
        return notices.stalled_told(job)
    except Exception:  # noqa: BLE001
        return False


def agent_status(job: Job, *, wait_kind: Any = ..., stalled: Optional[bool] = None) -> str:
    """The status of ``job`` in :data:`STATUSES`. ``wait_kind`` and
    ``stalled`` are read from the stores unless given."""
    if job.status == JobStatus.COMPLETED:
        return "idle"
    if job.status == JobStatus.CANCELLED:
        from openprogram.runtime.jobs.agent_result import LIMIT_STOPS
        return "failed" if job.reason_code in LIMIT_STOPS else "idle"
    if job.status == JobStatus.ERRORED:
        return "failed"
    kind = _open_wait_kind(job.id) if wait_kind is ... else wait_kind
    if kind == "approval":
        return "needs approval"
    if kind:
        return "needs input"
    if stalled if stalled is not None else (job.status == JobStatus.RUNNING and _was_stalled(job)):
        return "stalled"
    return "running"


def subtree_usage(
    job: Job, *, ledger: Any = None,
    jobs_in: Optional[Callable[[str], list[Job]]] = None,
) -> dict[str, Any]:
    """``{"tokens", "cost_usd"}`` of ``job`` and the helpers under it; a
    value the ledger cannot give is None (``cost_usd`` too when a price is
    unknown). ``jobs_in`` is ``agent_result.subtree_job_ids``'s session reader."""
    from openprogram.runtime.jobs.agent_result import _ledger_usage, subtree_job_ids
    from decimal import Decimal
    usage = _ledger_usage(subtree_job_ids(job, jobs_in=jobs_in), ledger)
    if usage is None:
        return {"tokens": None, "cost_usd": None}
    cost = None
    if not usage.get("unknown_cost_events"):
        total = sum((Decimal(str(v or 0)) for v in usage.get("cost_values") or ()), Decimal(0))
        cost = format(total.quantize(Decimal("0.000001")), "f")
    return {"tokens": int(usage.get("total_tokens") or 0), "cost_usd": cost}


def outgoing_sends(session_id: str, jobs: Iterable[Job]) -> list[dict[str, Any]]:
    """The messages this conversation sent to other conversations that
    have not been answered yet, oldest first: Activity's "→ Butler" rows."""
    rows = []
    for job in sorted(jobs, key=lambda j: j.created_at or 0.0):
        if (job.message_origin != "agent_delivery" or job.caller_session_id != session_id
                or job.parent_session_id == session_id
                or job.status not in (JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING)):
            continue
        rows.append({
            "kind": "outgoing",
            "job_id": job.id,
            "sender": session_id,
            "to_session": job.parent_session_id,
            "to": job.label or job.agent_id or job.parent_session_id,
            "status": "waiting" if job.status != JobStatus.RUNNING else agent_status(job),
            "sent_at": job.created_at,
        })
    return rows


def roster(session_id: str, jobs: Iterable[Job]) -> dict[str, Any]:
    """Activity's overview roster: for every Job of the conversation its
    status, whether it still waits for a free slot, and what its subtree
    used (tokens, cost, and its own tool calls), keyed by Job id; plus the
    unanswered messages sent to other conversations."""
    from openprogram.runtime.jobs.agent_result import _tool_calls
    listed = list(jobs)
    children: dict[str, list[Job]] = {}
    for job in listed:
        if job.parent_job_id:
            children.setdefault(job.parent_job_id, []).append(job)

    def ended(job: Job, seen: set[str]) -> bool:
        if job.id in seen:
            return True
        seen.add(job.id)
        return is_terminal(job.status) and all(ended(child, seen) for child in children.get(job.id, ()))

    agents: dict[str, dict[str, Any]] = {}
    for job in listed:
        key = (job.id, job.completed_at)
        with _settled_lock:
            row = _settled.get(key)
        if row is None:
            row = {
                "agent_status": agent_status(job),
                "queued": job.status in (JobStatus.PENDING, JobStatus.QUEUED),
                "usage": {**subtree_usage(job), "tool_calls": _tool_calls(job)},
            }
            if row["usage"]["tokens"] is not None and ended(job, set()):
                with _settled_lock:
                    _settled[key] = row
                    while len(_settled) > _SETTLED_ENTRIES:
                        _settled.popitem(last=False)
        agents[job.id] = row
    return {"agents": agents, "outgoing": outgoing_sends(session_id, listed)}


class RosterFacts:
    """The ``agent_roster_facts`` execution hook (``activity_runs``)."""

    def facts(self, job: Job) -> dict[str, Any]:
        return {"agent_status": agent_status(job), "usage": subtree_usage(job)}

    def outgoing(self, session_id: str, jobs: Iterable[Job]) -> list[dict[str, Any]]:
        return outgoing_sends(session_id, jobs)

    def roster(self, session_id: str, jobs: Iterable[Job]) -> dict[str, Any]:
        return roster(session_id, jobs)


__all__ = ["STATUSES", "RosterFacts", "agent_status", "outgoing_sends", "roster", "subtree_usage"]
