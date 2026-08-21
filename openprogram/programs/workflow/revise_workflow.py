"""Revise an existing workflow project and publish a new Git revision."""

from __future__ import annotations

from openprogram.agentic_programming import function as agentic_fn
from openprogram.agentic_programming.function import agentic_function

from ._generation import planner
from ._project import repository
from ._runtime import bindings


@agentic_function(
    input={
        "workflow_id": {"description": "The workflow project to revise"},
        "request": {
            "description": "The requested change to the workflow",
            "multiline": True,
        },
    },
)
def revise_workflow(workflow_id: str, request: str) -> dict:
    """Author and publish a new revision of one existing workflow project.

    Builds the candidate from the project's active revision; old
    revisions stay reachable and publishes to the same project are
    serialized. Failures and cancellation publish nothing.
    """
    index, base, _ = repository._active_project(workflow_id)
    candidate = planner._request_project_candidate(
        request,
        bindings._registered_agentic_functions(),
        session_id=agentic_fn.current_session_id(),
        agent_id="main",
        spawn_caller=agentic_fn.current_call_id() or None,
        base=base,
    )
    return repository._publish_candidate(
        candidate,
        project_id=index["project_id"],
        action="revise",
    )
