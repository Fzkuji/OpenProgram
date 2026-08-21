"""Create and publish a new workflow project."""

from __future__ import annotations

from openprogram.agentic_programming import function as agentic_fn
from openprogram.agentic_programming.function import agentic_function

from ._generation import planner
from ._project import repository
from ._runtime import bindings


@agentic_function(
    input={
        "task": {
            "description": "The class of tasks the new workflow must perform",
            "multiline": True,
        },
    },
)
def create_workflow(task: str) -> dict:
    """Author, validate, and atomically publish one new reusable workflow.

    Does not execute the user task and never modifies existing projects.
    Failures, cancellation, and terminal errors publish nothing.
    """
    candidate = planner._request_project_candidate(
        task,
        bindings._registered_agentic_functions(),
        session_id=agentic_fn.current_session_id(),
        agent_id="main",
        spawn_caller=agentic_fn.current_call_id() or None,
        require_new_name=True,
    )
    return repository._publish_candidate(candidate, project_id="", action="create")
