"""Search, choose reuse or create, then run a workflow."""

from __future__ import annotations

from openprogram.agentic_programming import function as agentic_fn
from openprogram.agentic_programming.function import agentic_function
from openprogram.programs.workflow.errors import InvalidWorkflow
from openprogram.programs.workflow._generation import planner
from openprogram.programs.workflow._runtime import execution
from openprogram.programs.workflow._runtime import state as run_state
import openprogram.programs.workflow.create_workflow as create_workflow_mod
import openprogram.programs.workflow.search_workflows as search_workflows_mod


@agentic_function(
    tool_visible=False,
    input={
        "task": {
            "description": "The task to search, select, and execute",
            "multiline": True,
        },
    },
)
def auto_workflow(task: str) -> dict:
    """User-only orchestration: search, select reuse or create, then run."""
    session_id = agentic_fn.current_session_id()
    spawn_caller = agentic_fn.current_call_id() or None
    run_id = run_state._new_run_id()
    instance = run_state._instance_dir(session_id, run_id)
    instance.mkdir(parents=True, exist_ok=False)
    state = {
        "run_id": run_id,
        "task": task,
        "status": "running",
        "executions": 0,
        "items": [],
        "revisions": [],
        "result": "",
        "last_error": "",
        "project_action": "auto",
    }
    run_state._save_state(instance / "state.json", state)
    candidates = search_workflows_mod.search_workflows(task).get("workflows") or []
    try:
        decision = planner._request_auto_decision(
            task,
            candidates,
            session_id=session_id,
            agent_id="main",
            spawn_caller=spawn_caller,
        )
    except BaseException as exc:
        run_state._mark_run_exception(instance, state, exc)
        raise
    try:
        if decision["action"] == "reuse":
            workflow_id = decision["workflow_id"]
            revision = next(
                row["revision"]
                for row in candidates
                if row["workflow_id"] == workflow_id
            )
        else:
            created = create_workflow_mod.create_workflow(task)
            workflow_id = created["workflow_id"]
            revision = created["revision"]
        executed = execution._run_published_workflow(
            task,
            workflow_id,
            revision,
            session_id=session_id,
            spawn_caller=spawn_caller,
            run_id=run_id,
            project_action=decision["action"],
        )
        if executed.get("status") == "failed":
            raise InvalidWorkflow(f"workflow run failed: {executed.get('run_id')}")
    except BaseException as exc:
        run_state._mark_run_exception(instance, state, exc)
        raise
    return {
        "action": decision["action"],
        **(
            {"missing_capability": decision["missing_capability"]}
            if decision["action"] == "create" and "missing_capability" in decision
            else {}
        ),
        "workflow_id": workflow_id,
        "workflow_revision": revision,
        "result": executed,
        "run_id": run_id,
    }
