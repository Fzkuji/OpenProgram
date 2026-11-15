"""Eligibility for abandoning an ownerless chat frame, never its external effects.

Admitting the new turn that continues an interrupted chat is the Agent
engine's job: ``openprogram.runtime.conversation.restart_turn`` registers it
as the ``recovery_turn_starter`` hook, which ``restart`` calls.
"""
from __future__ import annotations

from .conversation_scope import conversation_execution_scope
from .model import CommandKind, CommandStatus, ExecutionStatus, TERMINAL_EXECUTION_STATUSES
from .waits import DurableWaitStore


def recovery_state(store, execution, *, allow_cancel: bool = False) -> dict:
    records, parents = conversation_execution_scope(store, execution.session_id)
    descendants = []
    for child in records:
        if child.status in TERMINAL_EXECUTION_STATUSES or child.execution_id == execution.execution_id:
            continue
        parent = parents.get(child.execution_id)
        seen = {child.execution_id}
        while parent:
            if parent in seen:
                raise ValueError("Cyclic conversation execution ancestry")
            seen.add(parent)
            if parent == execution.execution_id:
                descendants.append(child.execution_id)
                break
            parent = parents.get(parent)
    state = {"active_children": descendants, "can_start_new_turn": False,
             "recovery_mode": None, "recovery_reason": None}
    if descendants:
        return dict(state, recovery_reason="active_children")
    if execution.current_attempt_id:
        return dict(state, recovery_reason="active_owner")
    if DurableWaitStore(store).list_open(execution_id=execution.execution_id):
        return dict(state, recovery_reason="pending_wait")
    pending = store.list_commands(execution.execution_id,
                                 statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING))
    if any(not allow_cancel or command.kind is not CommandKind.CANCEL for command in pending):
        return dict(state, recovery_reason="pending_control")
    if execution.status in TERMINAL_EXECUTION_STATUSES:
        from .effects import EffectStore
        restricted = bool(EffectStore(store).list_unresolved(execution.execution_id))
        return dict(state, can_start_new_turn=True,
                    recovery_mode="restricted_new_turn" if restricted else "new_turn")
    from openprogram.runtime.execution.slash_actions import slash_action_of
    payload = store.get_agent_turn_input(execution.execution_id)
    if (not payload or payload.get("kind") != "chat"
            or slash_action_of(payload.get("request", {})) is not None):
        return dict(state, recovery_reason="not_ordinary_chat")
    if (execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
            and execution.reason_code == "effect_reconciliation"):
        return dict(state, can_start_new_turn=True, recovery_mode="restricted_new_turn")
    if (execution.status is ExecutionStatus.PAUSED
            and execution.reason_code == "continuation_contract_mismatch"):
        return dict(state, can_start_new_turn=True, recovery_mode="new_turn")
    if allow_cancel and execution.status is ExecutionStatus.CANCELLING:
        from .effects import EffectStore
        if EffectStore(store).list_unresolved(execution.execution_id):
            return dict(state, can_start_new_turn=True, recovery_mode="restricted_new_turn")
    return dict(state, recovery_reason="checkpoint_or_control_required")
