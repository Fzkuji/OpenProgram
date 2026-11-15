"""Read canonical execution state without creating another execution owner."""
from __future__ import annotations


class GoalStopUnconfirmed(RuntimeError):
    """The Goal intent is saved, but cancellation could not be confirmed."""

    def __init__(self, goal: dict):
        super().__init__("Goal change saved. Execution stop is not confirmed; use /goal stop to retry.")
        self.goal = goal


def request_goal_stop(goal: dict, session_id: str) -> None:
    """Submit cancellation for this saved identity, never a session fallback."""
    observed = goal_execution_state(goal, session_id)
    if observed["status"] == "untracked" or observed["finished"]:
        return
    if observed["status"] == "unavailable":
        raise GoalStopUnconfirmed(goal)
    from openprogram.runtime.execution.run_control import (
        cancel_execution,
        ExecutionNotCancellable,
    )

    try:
        # Stop the root first to forbid new descendants. Include descendants
        # behind terminal intermediate parents, not just direct live children.
        for execution_id in [observed["execution_id"], *observed.get("active_children", [])]:
            try:
                result = cancel_execution(execution_id)
            except ExecutionNotCancellable:
                continue  # Rechecked below; a terminal parent may have children.
            if isinstance(result, dict) and result.get("issue_code") not in (
                None, "owner_not_local", "owner_not_active", "already_delivered",
                "already_terminal",
            ):
                raise GoalStopUnconfirmed(goal)
        latest = goal_execution_state(goal, session_id)
        if not latest["finished"] and latest["status"] != "cancelling":
            raise GoalStopUnconfirmed(goal)
    except Exception as exc:
        raise GoalStopUnconfirmed(goal) from exc


def goal_execution_state(goal: dict, session_id: str = "") -> dict:
    execution_id = str(goal.get("execution_id") or "")
    if not execution_id:
        return {"execution_id": None, "status": "untracked", "finished": None}
    unavailable = {"execution_id": execution_id, "status": "unavailable", "finished": False}
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.model import TERMINAL_EXECUTION_STATUSES

        store = default_store()
        execution = store.get_execution(execution_id)
        if execution is None or (session_id and execution.session_id != session_id):
            return unavailable
        from openprogram.runtime.execution.chat_recovery import recovery_state
        recovery = recovery_state(store, execution, allow_cancel=True)
        from openprogram.runtime.execution.public import _effect_summary
        provider_incomplete = _effect_summary(store, execution).get("provider_response_incomplete") is True
        finished = execution.status in TERMINAL_EXECUTION_STATUSES and not recovery["active_children"]
        source = store.get_execution_input(execution_id)
        return {
            "execution_id": execution_id,
            "session_id": execution.session_id,
            "msg_id": source.user_message_id if source else None,
            "status": execution.status.value,
            "status_version": execution.status_version,
            "finished": finished,
            "provider_response_incomplete": provider_incomplete,
            **recovery,
        }
    except Exception:
        return unavailable


def goal_control_state(goal: dict, session_id: str, observed: dict | None = None) -> dict:
    """Project action prerequisites; mutations still recheck current versions."""
    import openprogram.programs.tools.planning.goal as goals
    from . import chat, verification
    observed = observed if observed is not None else goal_execution_state(goal, session_id)
    status = goal.get("status")
    terminal = status in goals.TERMINAL_STATUSES - goals.RESUMABLE_STATUSES
    reasons = {"resume": None, "verify": None, "end": "goal_terminal" if terminal else None,
               "pause": None if status in goals.RUNNING_STATUSES else "goal_not_running"}
    controls = {"reasons": reasons, "operations": [], "waits": [], "processes": [],
                "active_children": list(observed.get("active_children") or [])}
    execution_reason = None
    verify_reason = "no_completed_work"
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.foreground import active_foreground_task
        from openprogram.runtime.execution.conversation_scope import conversation_executions
        from openprogram.runtime.execution.effects import EffectStore
        from openprogram.runtime.execution.waits import DurableWaitStore
        store = default_store()
        if observed["status"] == "unavailable":
            execution_reason = "execution_unavailable"
        elif not (observed["status"] == "untracked" or observed.get("finished") or observed.get("can_start_new_turn")):
            execution_reason = observed.get("recovery_reason") or "execution_not_finished"
        foreground = active_foreground_task(store, session_id)
        if foreground and foreground["execution_id"] != goal.get("execution_id"):
            execution_reason = "active_owner"
        effects, waits = EffectStore(store), DurableWaitStore(store)
        for member in conversation_executions(store, session_id):
            for effect in effects.list_unresolved(member.execution_id):
                if effect.metadata.get("kind") == "provider.before":
                    continue
                payload = effect.metadata.get("payload") or {}
                controls["operations"].append({
                    "effect_id": effect.effect_id, "execution_id": member.execution_id,
                    "status": effect.status.value, "tool_name": payload.get("tool_name") if isinstance(payload, dict) else None,
                    "created_at": effect.created_at, "dispatched_at": effect.dispatched_at,
                })
            controls["waits"].extend({"wait_id": wait.wait_id, "execution_id": member.execution_id,
                                      "kind": wait.kind, "created_at": wait.created_at}
                                     for wait in waits.list_open(execution_id=member.execution_id))
        controls["processes"] = verification.managed_work(store, session_id, goal)
        source = store.get_agent_turn_input(goal["execution_id"]) if goal.get("execution_id") else None
        if source and source.get("kind") == "chat" and observed.get("finished"):
            verify_reason = verification.blockers(store, session_id, goal["execution_id"], goal)
            if not verify_reason and any(item.get("status") != "completed" for item in chat.todos(session_id, goal)):
                verify_reason = "unfinished_todos"
    except Exception:
        execution_reason = verify_reason = "execution_unavailable"
    exhausted = goals.budget_exhausted(goal)
    budget_reason = "elapsed_time_unknown" if exhausted == "elapsed_time_unknown" else "budget_exhausted" if exhausted else None
    reasons["resume"] = ("goal_terminal" if terminal else
                         "goal_not_resumable" if status not in goals.RESUMABLE_STATUSES else
                         execution_reason or budget_reason)
    reasons["verify"] = "goal_terminal" if terminal else execution_reason or budget_reason or verify_reason
    controls.update({"can_" + action: reason is None for action, reason in reasons.items()})
    return controls


def goal_projection(goal: dict, session_id: str) -> dict:
    observed = goal_execution_state(goal, session_id)
    return {"goal": goal, "execution": observed, "controls": goal_control_state(goal, session_id, observed)}


def require_goal_execution_finished(goal: dict, session_id: str) -> None:
    """A Goal resumes only after its last execution stopped or can start a new turn."""
    from .state import GoalConflictError

    observed = goal_execution_state(goal, session_id)
    if observed["status"] == "untracked" or observed["finished"] or observed.get("can_start_new_turn"):
        return
    raise GoalConflictError(
        f"Previous Goal execution is {observed['status']}; "
        "resume requires a confirmed stop with no active child executions."
    )
