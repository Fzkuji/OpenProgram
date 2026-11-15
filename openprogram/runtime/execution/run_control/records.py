"""Execution records run control reads: DAG nodes, Job views, status vocabulary.

Run control predates the canonical store: a cancel still finds a legacy
execution as a session DAG node or a Job record. Job records belong to
``openprogram.runtime.jobs`` and are reached through the ``job_records``
hook.
"""
from __future__ import annotations

import time
from typing import Any

from .. import hooks
import logging

_log = logging.getLogger(__name__)


_TERMINAL_STATUSES = frozenset({
    "completed", "failed", "interrupted", "cancelled", "error",
})
_ACTIVE_STATUSES = frozenset({"queued", "running", "pending", "streaming"})
_CANCEL_INTENT_STATUSES = frozenset({"cancelling", "cancelled"})


def _execution_dto(session_id: str, node: Any) -> dict[str, Any]:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    return {
        "execution_id": node.id,
        "session_id": session_id,
        "parent_execution_id": node.caller or None,
        "execution_kind": metadata.get("execution_kind"),
        "status": metadata.get("status"),
        "reason_code": metadata.get("reason_code"),
        "cancellation_requested_at": metadata.get(
            "cancellation_requested_at",
        ),
        "finished_at": metadata.get("finished_at"),
        "partial_output_available": bool(node.output),
    }


def _get_node(store: Any, session_id: str, execution_id: str) -> Any | None:
    return next(
        (node for node in store.get_nodes(session_id) if node.id == execution_id),
        None,
    )


_JOB_STATUS_TO_EXEC = {
    "pending": "queued",
    "queued": "queued",
    "running": "running",
    "cancelled": "cancelled",
    "completed": "completed",
    "errored": "failed",
}


class _ExecutionView:
    """DTO-shaped view of a Job (or other non-DAG execution) for cancel."""

    __slots__ = ("id", "caller", "output", "metadata")

    def __init__(
        self,
        *,
        id: str,
        caller: str = "",
        output: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.caller = caller or ""
        self.output = output
        self.metadata = metadata or {}


def _job_execution_view(job: Any) -> _ExecutionView:
    raw = job.status.value if hasattr(job.status, "value") else str(job.status)
    status = _JOB_STATUS_TO_EXEC.get(raw, raw)
    if status == "running" and getattr(job, "cancel_requested_at", None):
        status = "cancelling"
    return _ExecutionView(
        id=job.id,
        caller=getattr(job, "parent_job_id", None) or "",
        output=getattr(job, "result_text", None)
        or getattr(job, "error", None)
        or getattr(job, "prompt", "")
        or "",
        metadata={
            "status": status,
            "reason_code": getattr(job, "reason_code", None),
            "execution_kind": "job",
            "cancellation_requested_at": getattr(
                job, "cancel_requested_at", None,
            ),
            "finished_at": getattr(job, "completed_at", None),
        },
    )


def _find_job(
    execution_id: str, *, session_id: str | None = None,
) -> Any | None:
    jobs = hooks.job_records()
    if session_id is not None:
        try:
            job = jobs.load(session_id, execution_id)
            if (
                job is not None
                and getattr(job, "parent_session_id", None) == session_id
            ):
                return job
        except Exception:
            # Lookup only: treated as no job.
            _log.debug("job %s lookup failed in session %s", execution_id, session_id, exc_info=True)
        return None

    try:
        job = jobs.live_job(execution_id)
        if job is not None:
            return job
    except Exception:
        # Lookup only: fall through to the session scan.
        _log.debug("live job lookup failed for %s", execution_id, exc_info=True)
    try:
        store = _canonical_store()
        for session in store.list_sessions(limit=10**9, include_archived=True):
            job = jobs.load(session["id"], execution_id)
            if job is not None:
                return job
    except Exception:
        # Lookup only: treated as no job.
        _log.debug("session scan for job %s failed", execution_id, exc_info=True)
    return None


def _canonical_store(store: Any | None = None) -> Any:
    if store is not None:
        return store
    try:
        from openprogram.state.store import _store
        writer = _store.get()
        bound = getattr(writer, "store", None)
        if bound is not None:
            return bound
    except Exception:
        # Fall back to the default session database.
        _log.debug("bound session writer unavailable", exc_info=True)
    from openprogram.runtime.context.session_db import default_db
    return default_db()


def _find_dag_execution(
    store: Any,
    execution_id: str,
    *,
    session_id: str | None = None,
    strict_session: bool = False,
) -> tuple[str, Any] | None:
    if session_id:
        node = _get_node(store, session_id, execution_id)
        if node is not None:
            return session_id, node
        if strict_session:
            return None
    try:
        sessions = store.list_sessions(limit=10**9, include_archived=True)
    except Exception:
        return None
    for session in sessions:
        sid = session["id"]
        node = _get_node(store, sid, execution_id)
        if node is not None:
            return sid, node
    return None


def _find_execution(
    store: Any, execution_id: str, *, session_id: str | None = None,
) -> tuple[str, Any] | None:
    found = _find_dag_execution(
        store,
        execution_id,
        session_id=session_id,
        strict_session=session_id is not None,
    )
    if found is not None:
        return found
    job = _find_job(execution_id, session_id=session_id)
    if job is not None:
        return (
            getattr(job, "parent_session_id", None) or session_id,
            _job_execution_view(job),
        )
    return None


def _caller_descendants(store: Any, session_id: str, execution_id: str) -> list[Any]:
    nodes = store.get_nodes(session_id)
    child_ids = {execution_id}
    descendants: list[Any] = []
    pending = list(nodes)
    while pending:
        added = False
        for node in pending[:]:
            if node.caller in child_ids:
                child_ids.add(node.id)
                descendants.append(node)
                pending.remove(node)
                added = True
        if not added:
            break
    return descendants


def _node_status(node: Any) -> str | None:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    return metadata.get("status")


def _cas_write_status(
    store: Any,
    session_id: str,
    node_id: str,
    *,
    from_statuses: frozenset[str],
    metadata: dict[str, Any],
) -> bool:
    live = _get_node(store, session_id, node_id)
    if live is None:
        return False
    current = _node_status(live)
    if current not in from_statuses:
        return False
    store.update_node(session_id, node_id, metadata=metadata)
    return True


def _finalize_projections(
    store: Any, session_id: str, execution_id: str, node: Any,
) -> None:
    reason_code = (
        (node.metadata or {}).get("reason_code") if node is not None else None
    ) or "cancel.user"
    jobs = hooks.job_records()
    try:
        job = jobs.live_job(execution_id)
        if job is not None and not jobs.is_terminal(job):
            cancelled = jobs.cancel_live(
                execution_id, reason="execution cancelled",
            )
            if cancelled is None or not jobs.is_terminal(cancelled):
                try:
                    jobs.update_status(
                        session_id,
                        execution_id,
                        "cancelled",
                        cancel_requested_at=time.time(),
                        reason_code=reason_code,
                    )
                except Exception:
                    # The DAG node is already cancelled; the job row may stay stale.
                    _log.warning("job %s not marked cancelled", execution_id, exc_info=True)
    except Exception:
        # Projection is secondary to the cancelled DAG node.
        _log.warning("job projection not finalized for %s", execution_id, exc_info=True)
