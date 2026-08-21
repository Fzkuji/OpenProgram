"""Inject llm, agent, goal, and control-flow callables into a workflow."""

from __future__ import annotations

import importlib
from typing import Callable, Optional


def _agent_function(session_id: str, spawn_caller: Optional[str]) -> Callable:
    from openprogram.agent.sub_agent_run import run_agent_turn

    def agent(
        prompt: str,
        description: str = "",
        agent_id: str = "",
        start_from: str = "clean",
        run_in_background: bool = False,
        to: str = "",
        archive_when_done: bool = False,
    ) -> str:
        if run_in_background:
            return "[agent error] run_in_background not supported in workflow context"
        if to:
            return "[agent error] to= dispatch not supported in workflow context"
        if archive_when_done:
            return "[agent error] archive_when_done not supported in workflow context"
        if start_from != "clean":
            return "[agent error] start_from must be 'clean' in workflow context"

        try:
            result = run_agent_turn(
                session_id=session_id,
                prompt=prompt,
                agent_id=agent_id or "main",
                branch_from=None,
                label=description or "workflow agent",
                spawn_caller=spawn_caller,
                advance_head=False,
                tools_override=None,
            )
            if result.failed:
                raise RuntimeError(result.error or "workflow agent turn failed")
            return result.final_text or ""
        except Exception as exc:
            return f"[agent error] {exc}"

    return agent


def _agent_loop_function() -> Callable:
    from openprogram.agentic_programming import agent

    return agent


def _llm_function() -> Callable:
    from openprogram.agentic_programming import llm

    return llm


def _goal_function() -> Callable:
    from openprogram.agentic_programming import goal

    return goal


def _validate_and_retry_function() -> Callable:
    from openprogram.agentic_programming.control_flow import validate_and_retry

    return validate_and_retry


def _route_function() -> Callable:
    from openprogram.agentic_programming.control_flow import route

    return route


def _conditional_function() -> Callable:
    from openprogram.agentic_programming.control_flow import conditional

    return conditional


def _registered_agentic_functions() -> dict[str, Callable]:
    """Resolve Python-callable entries defined by AGENTIC_MODULES."""
    from openprogram.programs._registry import AGENTIC_MODULES

    found: dict[str, Callable] = {}
    for module_name in AGENTIC_MODULES:
        if module_name == "workflow" or module_name in {
            "workflow.search_workflows",
            "workflow.create_workflow",
            "workflow.revise_workflow",
            "workflow.auto_workflow",
        }:
            continue
        try:
            module = importlib.import_module(
                f"openprogram.programs.functions.agentic.{module_name}"
            )
        except Exception:
            continue
        try:
            for value in vars(module).values():
                inner = getattr(value, "_fn", None)
                if inner is not None and inner.__module__ == module.__name__:
                    found[inner.__name__] = value
        except Exception:
            continue
    return found
