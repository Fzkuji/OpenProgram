"""JobRunner — ThreadPoolExecutor-backed worker pool.

Process-wide singleton. Jobs are submitted via :meth:`spawn_job`,
which returns immediately with a job id. The actual work runs in
the pool's worker thread by calling :func:`run_agent_turn` internally
(see ``sub_agent_run.py``).

Why a pool of OS threads instead of asyncio: every existing
``process_user_turn`` call already opens its own ``asyncio.new_event_loop``
inside the calling thread. Stacking a top-level asyncio scheduler
would double-loop. Threads also play nice with the synchronous
BashTool / file IO that dominates wall-clock time of a sub-agent.

Cancel commands are persisted through the canonical execution control service
and delivered to the exact attempt-bound driver handle.

Crash recovery: :func:`store.reconcile_orphans` runs at process start
(lazily, on first runner construction). Existing jobs left in
non-terminal state are flipped to ``errored``.

Broadcast events: each state transition fires a WS broadcast via
``openprogram.webui.server._broadcast`` (lazy import) so the UI
updates without an explicit poll. We also fire a ``session_reload``
on terminal so the existing attach card pickup path triggers.
"""


from __future__ import annotations


import atexit


import asyncio


import contextvars


from contextlib import contextmanager, nullcontext


import json


import logging


import os


import hashlib


import threading


import time


import traceback


import uuid


from concurrent.futures import Future, ThreadPoolExecutor


from dataclasses import dataclass, replace


from typing import Any, Callable, Mapping, Optional


from openprogram.agent.job.store import (
    list_jobs as _store_list,
    load_job as _store_load,
    reconcile_orphans as _store_reconcile,
    save_job as _store_save,
    update_job_status as _store_update_status,
)


from openprogram.events import emit_safe


from openprogram.agent.job.types import (
    Job,
    JobStatus,
    is_terminal,
    mint_job_id,
)


_log = logging.getLogger(__name__)


_DEFAULT_MAX_WORKERS = 4


_MAX_CANONICAL_JOB_INPUT_BYTES = 1_000_000


_CANCEL_ESCALATION_SECS = 30.0


_LEASE_RENEW_SECS = 10.0


_RECONCILE_SECS = 5.0


_RUNNERS_BY_EXECUTION_PATH: dict[str, "JobRunner"] = {}


_RUNNERS_BY_EXECUTION_LOCK = threading.Lock()


class NonPreemptibleOperation(RuntimeError):
    reason_code = "error.nonpreemptible_operation"


class JobOperationTimeout(asyncio.TimeoutError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _execution_failure_reason(error: str | None) -> str:
    text = error or ""
    for reason_code in (
        NonPreemptibleOperation.reason_code,
        "budget.runtime_exhausted",
        "budget.idle_exhausted",
    ):
        if reason_code in text:
            return reason_code
    return "error.execution"


def _terminal_fields(
    status: JobStatus,
    reason_code: str,
    *,
    head_id: str | None = None,
    result_text: str | None = None,
    error: str | None = None,
) -> dict[str, str | None]:
    return {
        "status": status.value,
        "head_id": head_id,
        "result_text": result_text,
        "error": error,
        "reason_code": reason_code,
    }


def _store_write_terminal(
    session_id: str,
    job_id: str,
    fields: dict[str, Any],
) -> Optional[Job]:
    terminal_fields = dict(fields)
    status = JobStatus(terminal_fields.pop("status"))
    return _store_update_status(session_id, job_id, status, **terminal_fields)


_current_job_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "openprogram_current_job_id", default=None,
)


_current_job_runner: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "openprogram_current_job_runner", default=None,
)


_borrowed_claim: contextvars.ContextVar[
    tuple[str, str, int, str] | None
] = contextvars.ContextVar("openprogram_borrowed_claim", default=None)


@dataclass(frozen=True)
class JobGovernanceContext:
    job_id: str
    budget_scope_id: str
    governor: Any
    ledger_identity: str
    effective_limits: tuple[tuple[str, int | str | None], ...]
    deadline_callback: Callable[[float | None], float | None]
    activity_callback: Callable[[str], bool]


_current_job_governance: contextvars.ContextVar[
    JobGovernanceContext | None
] = contextvars.ContextVar("openprogram_current_job_governance", default=None)


def record_current_job_activity(activity_kind: str) -> bool:
    runner = _current_job_runner.get()
    job_id = _current_job_id.get()
    return bool(runner and job_id and runner.record_job_activity(job_id, activity_kind))


def current_job_operation_timeout(
    declared_timeout: float | None,
    *,
    preemptibility: str = "async",
) -> float | None:
    runner = _current_job_runner.get()
    job_id = _current_job_id.get()
    if runner is None or job_id is None:
        return declared_timeout
    return runner.bounded_operation_timeout(
        job_id, declared_timeout, preemptibility=preemptibility,
    )


def current_job_operation_timeout_reason(
    declared_timeout: float | None,
) -> str | None:
    runner = _current_job_runner.get()
    job_id = _current_job_id.get()
    if runner is None or job_id is None:
        return None
    resolver = getattr(runner, "operation_timeout_reason", None)
    if not callable(resolver):
        return None
    return resolver(job_id, declared_timeout)


def current_job_resource_context() -> JobGovernanceContext | None:
    """Return the immutable governance handle for this claimed job body."""
    return _current_job_governance.get()


def _broadcast(payload: dict) -> None:
    """Send a WS frame to the frontend — best-effort.

    步 4：不再 import webui。把现成的帧 emit 到总线（``ws.frame`` 事件），
    webui 作为订阅者原样广播。帧内容（type / data 字段）一字不变，前端无感。
    """
    from openprogram.events import emit_ws_frame
    emit_ws_frame(payload)


def _broadcast_session_reload(session_id: str, *, reason: str = "job") -> None:
    _broadcast({
        "type": "session_reload",
        "data": {"session_id": session_id, "reason": reason},
    })


def _refresh_context_stats(session_id: str) -> None:
    """Re-estimate the context ring after the runner moved the graph.

    Same call every other out-of-turn graph mutation makes (compaction,
    checkout, branch delete). Best-effort and lazily imported: the runner
    also runs in CLI processes where no webui server exists.
    """
    try:
        from openprogram.webui.server import refresh_context_stats
        refresh_context_stats(session_id)
    except Exception:
        pass


def _stamp_job_change_owner(job: Job) -> None:
    """Persist actor/ownership facts on the child assistant change set."""
    if not job.head_id:
        return
    try:
        from openprogram.store.session.session_store import default_store

        store = default_store()
        store.merge_node_metadata(
            job.parent_session_id,
            job.head_id,
            {"change_owner": {
                "relation": job.relation,
                "origin_turn_id": job.origin_turn_id,
                "actor_id": job.agent_id,
                "job_id": job.id,
                "worktree_id": job.worktree_id,
                "session_id": job.parent_session_id,
                "status": job.status.value,
            }},
        )
    except Exception:
        _log.warning("job change ownership stamp failed for %s", job.id, exc_info=True)


def _mirror_linked_job_to_caller(job: Job) -> None:
    """Keep cross-session linked impact visible from its origin turn."""
    try:
        from openprogram.agent.job.store import mirror_linked_job_to_caller

        mirror_linked_job_to_caller(job)
    except Exception:
        _log.warning("linked job mirror failed for %s", job.id, exc_info=True)


def _broadcast_job_status(job: Job, resource: dict | None = None) -> None:
    data = {
        "job_id": job.id,
        "session_id": job.parent_session_id,
        "status": job.status.value,
        "parent_msg_id": job.parent_msg_id,
        "target_branch_head_id": job.target_branch_head_id,
        "head_id": job.head_id,
        "label": job.label,
        "subject": job.subject,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }
    if resource is not None:
        data["resource"] = resource
    _broadcast({
        "type": "job_status",
        "data": data,
    })
    # 事件层 tap：状态转移的单一漏斗，RUNNING → subagent.started，
    # 终止态 → subagent.ended。worker 线程里 ContextVar 不可靠，session 显式给。
    if job.status == JobStatus.RUNNING:
        emit_safe(
            "subagent.started", "system",
            {"job_id": job.id, "label": job.label},
            {"session": job.parent_session_id},
        )
    elif is_terminal(job.status):
        emit_safe(
            "subagent.ended", "system",
            {"job_id": job.id, "status": job.status.value, "error": job.error},
            {"session": job.parent_session_id},
        )


_runner_lock = threading.Lock()


_runner: Optional[JobRunner] = None


def get_runner() -> JobRunner:
    """Process-wide JobRunner. Idempotent."""
    from .service import JobRunner
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = JobRunner()
        return _runner


def runner_for_execution_store(store) -> JobRunner | None:
    """Return the local runner owning this execution store, if any."""
    from .service import JobRunner
    with _RUNNERS_BY_EXECUTION_LOCK:
        return _RUNNERS_BY_EXECUTION_PATH.get(str(store.path))


def shutdown_runner() -> None:
    """Tear down the singleton (mainly for tests)."""
    global _runner
    with _runner_lock:
        if _runner is not None:
            try:
                _runner.shutdown(wait=False)
            except Exception:
                pass
            _runner = None


atexit.register(shutdown_runner)


__all__ = [
    "NonPreemptibleOperation",
    "JobGovernanceContext",
    "JobRunner",
    "get_runner",
    "runner_for_execution_store",
    "shutdown_runner",
]

