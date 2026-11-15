"""A Workflow Goal across a worker restart.

A Workflow Goal whose execution lease belonged to the previous worker is
recoverable state, not a terminal error. Restart reconciliation persists an
explicit pause, so hydration shows the Goal content and a resume action instead
of a false running indicator or a discarded checkpoint. Chat Goals keep their
run intent: canonical recovery continues their execution, and a display repair
must not disable it.

The caller holds the session's Goal owner lock (``ownership.goal_owner``).
"""
from __future__ import annotations

import time

_RUNNING = {"refining", "active", "running", "evaluating"}


def pause_for_worker_restart(store, session_id: str) -> bool:
    """Pause a running Workflow Goal of ``session_id``; True when one was paused."""
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.runtime.execution import default_store

    session = store.get_session(session_id) or {}
    goal = (session.get("extra_meta") or {}).get("goal")
    if (not isinstance(goal, dict)
            or goal.get("execution_mode") == "chat"
            or goal.get("status") not in _RUNNING):
        return False
    goal = dict(goal)
    previous = default_store().get_execution(str(goal.get("execution_id") or ""))
    cutoff = previous.updated_at if previous is not None else time.time()
    goals.accumulate_goal_usage(session_id, goal, until=cutoff)
    goals.checkpoint_active_elapsed(goal, now=cutoff, stop=True)
    goal["status"] = "paused_recoverable"
    goal["phase"] = "paused"
    goal["recoverable"] = True
    goal["pause_reason"] = "worker_restart"
    goal["active_started_at"] = None
    goal["last_reason"] = (
        "worker restarted during Goal execution; resume from the latest checkpoint"
    )
    goals.save_goal(session_id, goal)
    goals._emit_goal_update(None, session_id, goal)
    return True
