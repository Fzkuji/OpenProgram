"""Versioned owner session permissions and operation-boundary snapshots.

SessionStore is the settings authority; canonical execution waits remain the
only approval authority. Admission identity and runtime contracts are immutable.
"""
from __future__ import annotations

import copy
from typing import Any

from openprogram.agent.session_config import VALID_PERMISSION


class PermissionUpdateError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def permission_state(session_id: str, *, db=None) -> dict[str, Any]:
    if db is None:
        from openprogram.agent.session_db import default_db
        db = default_db()
    row = db.get_session(session_id) or {}
    state = (row.get("extra_meta") or {}).get("permission_state") or row.get("permission_state")
    if state is not None:
        if not isinstance(state, dict) or state.get("mode") not in VALID_PERMISSION or type(state.get("version")) is not int:
            raise PermissionUpdateError("invalid_permission_state")
        return dict(state)
    from openprogram.agent.session_config import project_defaults
    mode = row.get("permission_mode") or project_defaults(session_id).get("permission_mode") or "ask"
    return {"mode": mode if mode in VALID_PERMISSION else "ask", "version": 0}


def update_permission(session_id: str, mode: str, expected_version: int, actor: dict, *, db=None) -> dict:
    from openprogram.agent.authority import normalize_authority
    authority = normalize_authority(actor)
    if not authority or authority["authority_tier"] != "owner" or authority["interaction"] != "interactive":
        raise PermissionUpdateError("permission_owner_required")
    if not isinstance(mode, str) or mode not in VALID_PERMISSION or type(expected_version) is not int or expected_version < 0:
        raise PermissionUpdateError("invalid_permission_update")
    if db is None:
        from openprogram.agent.session_db import default_db
        db = default_db()
    if db.get_session(session_id) is None:
        raise PermissionUpdateError("session_not_found")

    def update(current):
        if current.get("principal_id", authority["principal_id"]) != authority["principal_id"]:
            raise PermissionUpdateError("permission_owner_mismatch")
        if current.get("version", 0) != expected_version:
            raise PermissionUpdateError("permission_version_conflict")
        return {"mode": mode, "version": expected_version + 1, "principal_id": authority["principal_id"]}

    result = db.update_session_dict(session_id, "permission_state", update)
    if result is None:
        raise PermissionUpdateError("permission_update_failed")
    return result


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
    from openprogram.agent.internals._approval import wrap_with_approval
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
    from openprogram.agent.internals._approval import permission_decision
    from openprogram.execution import default_control_service
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
            await service.request_wait_answer(
                command_id=f"permission:{wait.wait_id}:{version}", execution_id=wait.execution_id,
                expected_version=execution.status_version, actor={
                    "principal_id": current.principal_id, "authority_tier": "owner",
                    "surface": "permission-setting", "permission_version": version,
                }, wait_id=wait.wait_id, generation=wait.claim_generation,
                answer={"answer": "approve", "scope": "once"},
            )
        except ExecutionConflict:
            # A concurrent answer, cancellation, or timeout owns the outcome.
            continue
