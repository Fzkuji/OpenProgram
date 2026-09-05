"""Transport-neutral public execution and Job resource projections."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from .model import (
    CommandStatus,
    EventCursor,
    ExecutionRecord,
    ExecutionSnapshot,
    JobResourceDTO,
)


_log = logging.getLogger(__name__)


def project_id_for_session(session_id: str) -> str:
    """Resolve the frozen project binding without trusting transport input."""
    try:
        from openprogram.store.project import project_for_session

        project = project_for_session(session_id)
        if project is not None:
            return project.id
    except Exception:
        _log.debug("project lookup failed for execution snapshot", exc_info=True)
    # Existing ad-hoc sessions use the default project.  A future admission
    # path may reject an unbound session; public reads remain non-authoritative.
    return "default"


def _event_sequence(store: Any, execution_id: str, fallback: int) -> int:
    try:
        events = store.list_events(execution_id)
        if events:
            return max(int(event.execution_sequence) for event in events)
    except Exception:
        _log.debug("event sequence lookup failed for execution snapshot", exc_info=True)
    return fallback


def _pending_commands(store: Any, execution_id: str) -> tuple[str, ...]:
    try:
        commands = store.list_commands(
            execution_id,
            statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING),
        )
        return tuple(command.command_id for command in commands)
    except Exception:
        return ()


def _active_children(store: Any, execution_id: str) -> tuple[str, ...]:
    try:
        return tuple(
            item.execution_id
            for item in store.list_nonterminal()
            if item.parent_execution_id == execution_id
        )
    except Exception:
        return ()


def _canonical_resource(
    resource: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    return dict(resource) if isinstance(resource, Mapping) else None


def execution_update_frame(
    execution: Mapping[str, Any],
    event_cursor: Mapping[str, Any],
    *,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical execution.updated transport envelope."""
    execution_data = dict(execution)
    cursor_data = dict(event_cursor)
    detail = dict(data or {})
    detail["execution"] = execution_data
    detail["event_cursor"] = cursor_data
    return {
        "type": "execution.updated",
        "execution": execution_data,
        "event_cursor": cursor_data,
        "data": detail,
    }


def execution_snapshot(
    execution: ExecutionRecord,
    *,
    store: Any,
    resource: Mapping[str, Any] | None = None,
    project_id: str | None = None,
    job_id: str | None = None,
    job: Any = None,
    event_sequence: int | None = None,
) -> ExecutionSnapshot:
    from .foreground import foreground_task_snapshot

    sequence = (event_sequence if event_sequence is not None
                else _event_sequence(store, execution.execution_id, execution.status_version))
    return ExecutionSnapshot(
        execution_id=execution.execution_id,
        job_id=job_id or execution.execution_id,
        run_id=execution.run_id,
        parent_execution_id=execution.parent_execution_id,
        project_id=project_id or getattr(job, "project_id", None)
        or project_id_for_session(execution.session_id),
        session_id=execution.session_id,
        revision_id=execution.revision_id,
        status=execution.status.value,
        status_version=execution.status_version,
        reason_code=execution.reason_code,
        current_attempt_id=execution.current_attempt_id,
        owner_lease=dict(execution.owner_lease) or None,
        resource=_canonical_resource(
            resource,
        ),
        checkpoint_head_id=execution.checkpoint_head_id,
        safe_point=dict(execution.safe_point) or None,
        capabilities=execution.capabilities.to_dict(),
        pending_command_ids=_pending_commands(store, execution.execution_id),
        active_child_ids=_active_children(store, execution.execution_id),
        effect_summary=dict(execution.effect_summary),
        terminal_at=execution.terminal_at,
        updated_at=execution.updated_at,
        event_sequence=sequence,
        foreground_task=foreground_task_snapshot(store, execution),
    )


def job_resource_dto(
    job: Any,
    *,
    execution: ExecutionRecord,
    resource: Mapping[str, Any] | None,
    store: Any,
    project_id: str | None = None,
) -> JobResourceDTO:
    snapshot = execution_snapshot(
        execution,
        store=store,
        resource=resource,
        project_id=project_id,
        job_id=job.id,
        job=job,
    )
    snapshot_data = snapshot.to_dict()
    return JobResourceDTO(
        job_id=job.id,
        execution_id=execution.execution_id,
        project_id=snapshot.project_id,
        session_id=execution.session_id,
        parent_execution_id=execution.parent_execution_id,
        label=str(getattr(job, "label", None) or getattr(job, "subject", None) or ""),
        subject=str(getattr(job, "subject", None) or ""),
        prompt_summary=str(getattr(job, "prompt", None) or "")[:240],
        relation=str(getattr(job, "relation", None) or "owned"),
        origin_turn_id=getattr(job, "origin_turn_id", None),
        status=execution.status.value,
        status_version=execution.status_version,
        capabilities=execution.capabilities.to_dict(),
        checkpoint_head_id=execution.checkpoint_head_id,
        resource=snapshot.resource,
        event_cursor=EventCursor(
            execution_id=execution.execution_id,
            next_sequence=snapshot.event_sequence + 1,
            snapshot_status_version=execution.status_version,
        ),
        execution=snapshot_data,
    )


__all__ = [
    "execution_update_frame",
    "execution_snapshot",
    "job_resource_dto",
    "project_id_for_session",
]
