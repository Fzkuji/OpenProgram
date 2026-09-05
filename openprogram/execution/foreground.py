"""Read-only foreground controls derived from durable Agent executions."""
from __future__ import annotations

from .model import ExecutionRecord, ExecutionStatus, TERMINAL_EXECUTION_STATUSES


def foreground_task_snapshot(store, execution: ExecutionRecord) -> dict | None:
    """Return controls for an active interactive turn, never a background Job."""
    if execution.status in TERMINAL_EXECUTION_STATUSES or execution.status is ExecutionStatus.PAUSED:
        return None
    payload = store.get_agent_turn_input(execution.execution_id)
    if payload is None:
        return None
    request = payload.get("request", {}) if payload.get("kind") == "chat" else payload
    if request.get("source") not in {"web", "tui", "acp"}:
        return None
    if request.get("interaction") not in {None, "interactive"}:
        return None
    source = store.get_execution_input(execution.execution_id)
    if source is None or not source.user_message_id:
        return None
    return {
        "session_id": execution.session_id,
        "msg_id": source.user_message_id,
        "execution_id": execution.execution_id,
        "status_version": execution.status_version,
        "status": execution.status.value,
        "func_name": "agent" if payload.get("kind") == "chat" else payload.get("tool_name", "function"),
        "started_at": execution.created_at,
    }


def active_foreground_task(store, session_id: str) -> dict | None:
    """Prefer the newest admitted foreground turn when branches share a session."""
    for execution in reversed(store.list_nonterminal(session_id=session_id)):
        task = foreground_task_snapshot(store, execution)
        if task is not None:
            return task
    return None
