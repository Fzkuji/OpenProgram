"""Public Goal Workflow: run work until the session Goal judge accepts it.

Other Workflows call :func:`goal`. Session ``/goal`` uses the same
judge, spec refinement, continuation loop, and persistence in this
package.
"""
from __future__ import annotations

from openprogram.agentic_programming.function import agentic_function


@agentic_function
def goal(
    prompt: str,
    condition: str,
    *,
    model: str = "",
    effort: str = "",
    max_rounds: int = 10,
    timeout_s: float | None = None,
) -> str:
    """Run a goal loop: agent works, the Goal judge checks completion.

    Args:
        prompt: Task prompt for the agent
        condition: Success condition to judge
        model: Model override for the working agent
        effort: Reasoning effort override
        max_rounds: Max judgment rounds
        timeout_s: Timeout per agent round

    Returns:
        Final result when the condition is met, or the last result if
        max_rounds is reached.
    """
    from openprogram.agentic_programming.agent import agent
    import openprogram.programs.workflow.goal as _goal

    last_result = ""
    for _round_num in range(max_rounds):
        last_result = agent(
            prompt=prompt,
            model=model,
            effort=effort,
            timeout_s=timeout_s,
        )
        try:
            verdict = _goal.judge_goal(goal=condition)
        except Exception:
            continue
        if verdict.get("met"):
            return last_result
    return last_result


__all__ = ["goal"]
