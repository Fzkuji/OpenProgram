"""Resume a workflow run from its saved snapshot."""

from __future__ import annotations

from openprogram.agentic_programming import function as agentic_fn

from ._runtime import execution
from ._runtime import state as run_state


def resume_workflow(run_id: str, **_deprecated) -> dict:
    """Resume one explicit workflow instance by id (internal use).

    Note:
        Old parameters (session_id, spawn_caller, agent_id) are deprecated
        and accepted via **_deprecated for backward compatibility.
    """
    sid = _deprecated.get("session_id") or agentic_fn.current_session_id()
    instance = run_state._instance_dir(sid, run_id)
    state = run_state._load_state(instance / "state.json")
    return execution._execute_workflow(
        state["task"],
        session_id=sid,
        agent_id=_deprecated.get("agent_id") or "main",
        spawn_caller=_deprecated.get("spawn_caller")
        or agentic_fn.current_call_id()
        or None,
        run_id=run_id,
    )
