"""Read canonical execution state without creating another execution owner."""
from __future__ import annotations


def goal_execution_state(goal: dict, session_id: str = "") -> dict:
    execution_id = str(goal.get("execution_id") or "")
    if not execution_id:
        return {"execution_id": None, "status": "untracked", "finished": None}
    unavailable = {"execution_id": execution_id, "status": "unavailable", "finished": False}
    try:
        from openprogram.execution import default_store
        from openprogram.execution.model import TERMINAL_EXECUTION_STATUSES

        store = default_store()
        execution = store.get_execution(execution_id)
        if execution is None or (session_id and execution.session_id != session_id):
            return unavailable
        active = store.list_nonterminal(session_id=execution.session_id)
        known = {item.execution_id: item for item in active}
        known[execution_id] = execution
        descendants = []
        for child in active:
            if child.execution_id == execution_id:
                continue
            parent_id = child.parent_execution_id
            seen = {child.execution_id}
            while parent_id:
                if parent_id == execution_id:
                    descendants.append(child.execution_id)
                    break
                if parent_id in seen:
                    return unavailable
                seen.add(parent_id)
                parent = known.get(parent_id)
                if parent is None:
                    parent = store.get_execution(parent_id)
                    if parent is None or parent.session_id != execution.session_id:
                        return unavailable
                    known[parent_id] = parent
                parent_id = parent.parent_execution_id
        return {
            "execution_id": execution_id,
            "status": execution.status.value,
            "status_version": execution.status_version,
            "active_children": descendants,
            "finished": execution.status in TERMINAL_EXECUTION_STATUSES and not descendants,
        }
    except Exception:
        return unavailable


def require_goal_execution_finished(
    goal: dict, session_id: str, *, current_execution_id: str | None = None,
) -> None:
    from .state import GoalConflictError

    observed = goal_execution_state(goal, session_id)
    # Script-only Goals have no canonical record. The public entry's existing
    # exclusive_goal lock is still required and excludes concurrent controllers.
    if observed["status"] == "untracked" or observed["finished"]:
        return
    if (
        current_execution_id and current_execution_id == observed["execution_id"]
        and observed["status"] == "running" and not observed.get("active_children")
        and goal.get("pause_reason") not in {"user", "edited"}
        and not goal.get("stop_requested")
    ):
        # Sequential invocations inside the same parent execution are not a
        # second execution. A user stop must never take this exception.
        return
    raise GoalConflictError(
        f"Previous Goal execution is {observed['status']}; "
        "resume requires a confirmed stop with no active child executions."
    )
