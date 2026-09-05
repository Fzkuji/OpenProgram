"""Live operation snapshots and canonical approval-wait reconciliation."""
from __future__ import annotations

import copy

from .state import permission_state


def current_permission_request(request):
    """Snapshot the live override only for its authenticated local owner."""
    req = copy.copy(request)
    if req.source not in {"web", "tui", "acp"} or req.authority_tier != "owner" or req.interaction != "interactive":
        return req
    state = permission_state(req.session_id)
    if state.get("principal_id") == req.principal_id:
        req.permission_mode = state["mode"]
        req._permission_version = state["version"]
    return req


def wrap_live_permission(tool, request, on_event):
    """Pin a decision per operation, without modifying the admission request.

    A safe-point suspension leaves at most one pending wrapper per tool.
    Continuation rebuilds it using the latest confirmed policy.
    """
    from openprogram.agent.permissions.approval import wrap_with_approval
    pending = None
    wrapped = copy.copy(tool)

    def for_call():
        return wrap_with_approval(tool, current_permission_request(request), on_event, _live=False)

    def manifest(call_id, args):
        nonlocal pending
        inner = for_call()
        pending = (call_id, inner)
        return inner._interaction_manifest(call_id, args)

    async def execute(call_id, args, cancel, on_update):
        nonlocal pending
        inner = pending[1] if pending is not None and pending[0] == call_id else for_call()
        pending = None
        return await inner.execute(call_id, args, cancel, on_update)

    wrapped.execute = execute
    wrapped._interaction_manifest = manifest
    return wrapped


async def reconcile_permission_waits(session_id: str, *, service=None) -> None:
    """Resume only mode-dependent approvals through canonical wait commands."""
    from types import SimpleNamespace
    from openprogram.agent.production_driver import AgentActivationService
    from openprogram.agent.permissions.policy import permission_decision
    from openprogram.execution import default_control_service
    from openprogram.events import emit_ws_frame
    from openprogram.execution.model import CommandStatus
    from openprogram.execution.store import ExecutionConflict
    from openprogram.execution.waits import DurableWaitStore
    from openprogram.worktree.context import set_worktree, reset_worktree

    service = service or default_control_service()
    store = service.executions
    resolver = AgentActivationService(lambda record: store.get_agent_turn_input(record.execution_id))
    for wait in DurableWaitStore(store).list_open(session_id=session_id):
        if wait.kind != "approval" or wait.request.get("approval_reason") != "MODE_APPROVAL":
            continue
        execution = store.get_execution(wait.execution_id)
        if execution is None or store.get_agent_turn_input(wait.execution_id) is None:
            continue
        from openprogram.agent.run_control import current_token
        if current_token(session_id, execution_id=wait.execution_id) is not None:
            # The driver repeats reconciliation after its old frame exits.
            continue
        request = resolver.build_request(execution, None)
        current = current_permission_request(request)
        version = getattr(current, "_permission_version", 0)
        if version <= wait.request.get("permission_version", 0):
            continue
        tool = SimpleNamespace(name=wait.request.get("tool"), _accept_edits_safe=wait.request.get("accept_edits_safe", False))
        token = set_worktree(wait.request.get("working_dir"))
        try:
            decision = permission_decision(tool, current, dict(wait.request.get("args") or {}))[0]
        finally:
            reset_worktree(token)
        if decision not in {"allow", "auto"}:
            continue
        try:
            dispatch = await service.request_wait_answer(
                command_id=f"permission:{wait.wait_id}:{version}", execution_id=wait.execution_id,
                expected_version=execution.status_version, actor={
                    "principal_id": current.principal_id, "authority_tier": "owner",
                    "surface": "permission-setting", "permission_version": version,
                }, wait_id=wait.wait_id, generation=wait.claim_generation,
                answer={"answer": "approve", "scope": "once"},
            )
            if dispatch.command.status is CommandStatus.APPLIED:
                emit_ws_frame({"type": "question.replied", "data": {
                    "id": wait.wait_id, "session_id": session_id,
                    "execution_id": wait.execution_id,
                }})
        except ExecutionConflict:
            # A concurrent answer, cancellation, or timeout owns the outcome.
            continue
