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

Broadcast events: each state transition fires a client broadcast so the UI
updates without an explicit poll. The attach card a job writes reaches open
transcripts through the transcript publisher.
"""


from __future__ import annotations


import asyncio  # read by the runner modules as shared.asyncio
import atexit




import contextvars






import logging






import threading










from dataclasses import dataclass


from typing import TYPE_CHECKING, Any, Callable, Optional


# The sibling operation modules call these through ``shared._store_*`` so the
# job store can be patched in one place (tests replace ``shared._store_load``).
from openprogram.runtime.jobs.store import (  # noqa: F401 - store seam read as shared._store_*
    list_jobs as _store_list,
    load_job as _store_load,
    reconcile_orphans as _store_reconcile,
    save_job as _store_save,
    update_job_status as _store_update_status,
)


from openprogram.runtime.events import emit_safe


from openprogram.runtime.jobs.types import (
    Job,
    JobStatus,
    is_terminal,
)

if TYPE_CHECKING:
    from openprogram.runtime.jobs.runner.service import JobRunner


_log = logging.getLogger(__name__)


from openprogram.runtime.jobs.resource_governance.limits import (  # noqa: E402
    DEFAULT_JOB_WORKERS as _DEFAULT_MAX_WORKERS,
)


_MAX_CANONICAL_JOB_INPUT_BYTES = 1_000_000


_CANCEL_ESCALATION_SECS = 30.0


_RECONCILE_SECS = 5.0


#: How long a session with nothing unfinished stays in memory after its last change.
_FINISHED_SESSION_MEMORY_SECS = 3600.0


_RUNNERS_BY_EXECUTION_PATH: dict[str, "JobRunner"] = {}


_RUNNERS_BY_EXECUTION_LOCK = threading.Lock()


class NonPreemptibleOperation(RuntimeError):
    reason_code = "error.nonpreemptible_operation"


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


def _turn_head_id(execution_store: Any, job_id: str, result: Any) -> str | None:
    """The Job's branch tip: the reply its turn wrote.

    The canonical driver hands back the turn runner's ``TurnResult``, which
    names that reply ``assistant_msg_id``. A turn that raised has no result,
    so the reply id the Job was admitted with stands in.
    """
    head_id = getattr(result, "head_id", None) or getattr(result, "assistant_msg_id", None)
    if head_id:
        return head_id
    try:
        record = execution_store.get_execution_input(job_id)
    except Exception:
        return None
    return getattr(record, "assistant_message_id", None) or None


def _store_write_terminal(
    session_id: str,
    job_id: str,
    fields: dict[str, Any],
) -> Optional[Job]:
    terminal_fields = dict(fields)
    status = JobStatus(terminal_fields.pop("status"))
    return _store_update_status(session_id, job_id, status, **terminal_fields)


# Set on the calls a scheduled run makes to the Worker: whose run it is, as the
# Worker verified it. Its turn lives in the run's own process.
scheduled_caller: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "openprogram_scheduled_caller", default=None,
)
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
    from openprogram.runtime.events import emit_ws_frame
    emit_ws_frame(payload)


def _refresh_context_stats(session_id: str) -> None:
    """Re-estimate the context ring after the runner moved the graph.

    Same call every other out-of-turn graph mutation makes (compaction,
    checkout, branch delete). Best-effort and lazily imported: the runner
    also runs in CLI processes where no webui server exists.
    """
    try:
        from openprogram.runtime import interface_link
        interface_link.refresh_context_stats(session_id)
    except Exception:
        # CLI processes have no webui server to refresh.
        _log.debug("context stats not refreshed for session %s", session_id, exc_info=True)


def _mirror_linked_job_to_caller(job: Job) -> None:
    """Keep cross-session linked impact visible from its origin turn."""
    try:
        from openprogram.runtime.jobs.store import mirror_linked_job_to_caller

        mirror_linked_job_to_caller(job)
    except Exception:
        _log.warning("linked job mirror failed for %s", job.id, exc_info=True)


_JOB_REPLY_SUFFIX = "_user_reply"


def _continued_job(job: Job) -> str | None:
    """The Job whose reply this Job's agent continues from: admission names that reply ``<job>_user_reply``."""
    parent = job.parent_msg_id or ""
    if not parent.endswith(_JOB_REPLY_SUFFIX):
        return None
    continued = parent[: -len(_JOB_REPLY_SUFFIX)]
    return continued if continued and continued != job.id else None


def _broadcast_job_status(job: Job, resource: dict | None = None) -> None:
    data = {
        "job_id": job.id,
        "session_id": job.parent_session_id,
        "status": job.status.value,
        "parent_msg_id": job.parent_msg_id,
        "continues_job_id": _continued_job(job),
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
    """Process-wide JobRunner. Idempotent.

    A function subprocess gets a stand-in that hands each call to the Worker.
    """
    from openprogram.runtime.execution.worker_calls import worker_calls
    calls = worker_calls()
    if calls is not None:
        from openprogram.runtime.jobs.runner.worker_runner import WorkerRunner
        return WorkerRunner(calls)
    from openprogram.runtime.jobs.runner.service import JobRunner
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = JobRunner()
        return _runner


def current_runner() -> JobRunner | None:
    """The process-wide JobRunner if one has started; never starts one."""
    with _runner_lock:
        return _runner


def runner_for_execution_store(store) -> JobRunner | None:
    """Return the local runner owning this execution store, if any."""
    with _RUNNERS_BY_EXECUTION_LOCK:
        return _RUNNERS_BY_EXECUTION_PATH.get(str(store.path))


def shutdown_runner(*, wait: bool = True) -> None:
    """Tear down the singleton (mainly for tests).

    Tests wait for pool workers so the unit thread-leak guard can see a
    stopped runner. Process exit uses ``wait=False`` so atexit cannot hang
    on a still-running job. The singleton lock is released before joining
    so worker threads that re-enter ``get_runner`` cannot deadlock.
    """
    global _runner
    with _runner_lock:
        runner = _runner
        _runner = None
    if runner is not None:
        try:
            runner.shutdown(wait=wait)
        except Exception:
            # Shutdown at exit must not raise.
            _log.debug("job runner shutdown failed", exc_info=True)


atexit.register(lambda: shutdown_runner(wait=False))


__all__ = [
    "NonPreemptibleOperation",
    "JobGovernanceContext",
    "current_runner",
    "get_runner",
    "runner_for_execution_store",
    "shutdown_runner",
]
