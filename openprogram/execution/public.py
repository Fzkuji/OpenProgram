"""Transport-neutral public execution and Job resource projections."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from .model import (
    CommandKind,
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


def _display_metadata(store: Any, execution: ExecutionRecord, job: Any) -> dict[str, Any] | None:
    """Project names and message associations, never input text or arguments."""
    try:
        source = store.get_execution_input(execution.execution_id)
        payload = store.get_agent_turn_input(execution.execution_id) or {}
        request = payload.get("request", {}) if payload.get("kind") == "chat" else payload
        tool_name = payload.get("tool_name")
        agent_id = request.get("agent_id") if isinstance(request, Mapping) else None
        label = getattr(job, "label", None) or tool_name or agent_id
        if source is None and not label:
            return None
        return {
            "kind": "job_agent" if job is not None else payload.get("kind"),
            "label": label if isinstance(label, str) else None,
            "entrypoint": source.entrypoint if source else None,
            "tool_name": tool_name if isinstance(tool_name, str) else None,
            "user_message_id": source.user_message_id if source else None,
            "assistant_message_id": source.assistant_message_id if source else None,
        }
    except Exception:
        _log.debug("display metadata lookup failed for execution snapshot", exc_info=True)
        return None


def _resume_eligibility(store: Any, execution: ExecutionRecord) -> tuple[bool, bool]:
    """Expose current resume prerequisites; command/CAS remains authoritative."""
    from contextlib import closing
    from .checkpoints import ExecutionCheckpointStore
    from .control import RuntimeControlService
    from .model import ExecutionStatus

    if execution.status is not ExecutionStatus.PAUSED or execution.current_attempt_id is not None:
        return False, False
    try:
        with closing(store._connect()) as connection:
            connection.execute("BEGIN")
            current = store._get_execution(connection, execution.execution_id)
            if current is None or current.status_version != execution.status_version:
                return False, False
            if connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution.execution_id,),
            ).fetchone() is not None:
                return False, False
            checkpoint_id = execution.checkpoint_head_id or execution.source_checkpoint_id
            checkpoint = (ExecutionCheckpointStore(store)._get(connection, checkpoint_id)
                          if checkpoint_id else None)
            if checkpoint is not None:
                if (checkpoint.execution_id != execution.execution_id
                        or checkpoint.revision_id != execution.revision_id):
                    if (connection.execute("SELECT 1 FROM attempts WHERE execution_id = ? LIMIT 1", (execution.execution_id,)).fetchone()
                            or not RuntimeControlService._agent_branch_checkpoint_is_valid(store, connection, execution, checkpoint)):
                        return False, False
                return (execution.capabilities.pause,
                        execution.capabilities.step
                        and not RuntimeControlService._agent_step_has_no_next_action(checkpoint))
            previous_attempt = connection.execute(
                "SELECT 1 FROM attempts WHERE execution_id = ? LIMIT 1",
                (execution.execution_id,),
            ).fetchone()
            return execution.capabilities.pause and previous_attempt is None, False
    except Exception:
        _log.debug("resume eligibility lookup failed for execution snapshot", exc_info=True)
        return False, False


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


def _effect_summary(store: Any, execution: ExecutionRecord) -> dict[str, Any]:
    """Separate missing model responses from user-actionable external effects."""
    summary = dict(execution.effect_summary)
    summary.pop("provider_response_incomplete", None)
    if (execution.status.value != "reconciliation_required"
            or execution.reason_code != "effect_reconciliation"
            or execution.current_attempt_id is not None or execution.owner_lease):
        return summary
    try:
        from .effects import EffectStore
        from .waits import DurableWaitStore

        unresolved = EffectStore(store).list_unresolved(execution.execution_id)
        if (unresolved and all(effect.metadata.get("kind") == "provider.before" for effect in unresolved)
                and not any(command.kind is not CommandKind.CANCEL for command in store.list_commands(
                    execution.execution_id, statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING),
                ))
                and not DurableWaitStore(store).list_open(execution_id=execution.execution_id)):
            summary["provider_response_incomplete"] = True
    except Exception:
        # Missing evidence must not suppress a real attention item.
        _log.debug("provider outcome classification unavailable", exc_info=True)
    return summary


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
    can_continue, can_step = _resume_eligibility(store, execution)
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
        effect_summary=_effect_summary(store, execution),
        terminal_at=execution.terminal_at,
        updated_at=execution.updated_at,
        event_sequence=sequence,
        foreground_task=foreground_task_snapshot(store, execution),
        display=_display_metadata(store, execution, job),
        can_continue=can_continue,
        can_step=can_step,
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


def public_event(event) -> dict:
    """Bounded, redacted event projection shared by REST and WebSocket replay."""
    from openprogram.execution.audit import redact_audit_payload

    payload = event.payload
    if event.kind.startswith("effect.") and isinstance(payload.get("effect"), dict):
        effect = payload["effect"]
        # A provider effect can contain the full runtime/tool contract. It is
        # durable evidence, not a UI frame; replaying it repeatedly can exceed
        # websocket recovery limits. Keep the persisted event unchanged.
        metadata = effect.get("metadata") or {}
        payload = {"effect": {
            **{key: effect.get(key) for key in (
                "effect_id", "execution_id", "status", "classification",
                "created_at", "updated_at", "dispatched_at", "resolved_at",
            )},
            "metadata": {key: metadata.get(key) for key in ("kind", "tool_name")},
        }}
    return {
        "sequence": event.execution_sequence,
        "execution_id": event.execution_id,
        "kind": event.kind,
        "payload": redact_audit_payload(payload),
        "execution_version": event.execution_version,
        "command_id": event.command_id,
    }
