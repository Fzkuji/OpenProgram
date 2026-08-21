"""Test namespace for workflow internals after the package split.

``monkeypatch.setattr(TL, name, value)`` writes through to the defining
module so production late-bound lookups see the patch.
"""

from __future__ import annotations

import importlib

from openprogram.agentic_programming import function as agentic_fn
from openprogram.programs.functions.agentic import workflow as pkg
from openprogram.programs.functions.agentic.workflow import errors

_PKG = "openprogram.programs.functions.agentic.workflow"
auto_workflow_mod = importlib.import_module(f"{_PKG}.auto_workflow")
create_workflow_mod = importlib.import_module(f"{_PKG}.create_workflow")
resume_workflow_mod = importlib.import_module(f"{_PKG}.resume_workflow")
revise_workflow_mod = importlib.import_module(f"{_PKG}.revise_workflow")
search_workflows_mod = importlib.import_module(f"{_PKG}.search_workflows")
from openprogram.programs.functions.agentic.workflow._generation import planner
from openprogram.programs.functions.agentic.workflow._generation import prompts
from openprogram.programs.functions.agentic.workflow._project import catalog
from openprogram.programs.functions.agentic.workflow._project import repository
from openprogram.programs.functions.agentic.workflow._project import validation
from openprogram.programs.functions.agentic.workflow._runtime import bindings
from openprogram.programs.functions.agentic.workflow._runtime import execution
from openprogram.programs.functions.agentic.workflow._runtime import state

_OWNERS = {
    "AUTO_DECISION_ATTEMPTS": prompts,
    "DELIVERY_INSTRUCTIONS": prompts,
    "InvalidWorkflow": errors,
    "MAX_ITEMS_EXECUTED": state,
    "PLANNER_TOOLS": prompts,
    "PROJECT_AUTHOR_ATTEMPTS": prompts,
    "WorkflowExecutionCapped": errors,
    "_agent_function": bindings,
    "_agent_loop_function": bindings,
    "_author_prompt": prompts,
    "_checkout_head": repository,
    "_direct_result_requested": state,
    "_extract_source": planner,
    "_git": repository,
    "_goal_function": bindings,
    "_llm_function": bindings,
    "_load_state": state,
    "_publish_snapshot": repository,
    "_read_project_index": catalog,
    "_read_repository_metadata": catalog,
    "_registered_agentic_functions": bindings,
    "_replace_snapshot": repository,
    "_request_auto_decision": planner,
    "_request_project_candidate": planner,
    "_resolve_workflow_dependencies": repository,
    "_run_planner_turn": planner,
    "_run_project_instance_locked": execution,
    "_run_published_workflow": execution,
    "_save_state": state,
    "_session_repo": state,
    "_summarize_workflow": state,
    "_validate_legacy_project_candidate": validation,
    "_validate_project_candidate": validation,
    "_validated_reply": planner,
    "_workflow_import_catalog": planner,
    "_workflow_projects_root": catalog,
    "_write_candidate_directory": repository,
    "auto_workflow": auto_workflow_mod,
    "create_workflow": create_workflow_mod,
    "current_session_id": agentic_fn,
    "resume_workflow": resume_workflow_mod,
    "revise_workflow": revise_workflow_mod,
    "search_workflows": search_workflows_mod,
    "shutil": repository,
    "subprocess": repository,
    "tomllib": catalog,
}

_FALLBACK = (
    pkg,
    auto_workflow_mod,
    create_workflow_mod,
    resume_workflow_mod,
    revise_workflow_mod,
    search_workflows_mod,
    errors,
    planner,
    prompts,
    catalog,
    repository,
    validation,
    bindings,
    execution,
    state,
    agentic_fn,
)


def _owner_for(name: str):
    owner = _OWNERS.get(name)
    if owner is not None:
        return owner
    for module in _FALLBACK:
        if name in vars(module):
            return module
    return None


class _TL:
    def __setattr__(self, name: str, value: object) -> None:
        owner = _owner_for(name)
        if owner is None:
            object.__setattr__(self, name, value)
            return
        setattr(owner, name, value)

    def __getattr__(self, name: str):
        owner = _owner_for(name)
        if owner is not None:
            return getattr(owner, name)
        raise AttributeError(name)


TL = _TL()
