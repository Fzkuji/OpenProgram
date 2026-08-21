"""Goal: judgment loop = repeatedly call agent + judge condition until met."""
from __future__ import annotations

from typing import Any

from openprogram.providers.structured_output import JsonSchemaOutput


_GOAL_JUDGMENT_FORMAT = JsonSchemaOutput(
    schema={
        "type": "object",
        "properties": {
            "met": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["met", "reason"],
        "additionalProperties": False,
    },
    name="goal_judgment",
    max_validation_retries=1,
)


def goal(
    prompt: str,
    condition: str,
    *,
    model: str = "",
    effort: str = "",
    max_rounds: int = 10,
    timeout_s: float | None = None,
) -> str:
    """Run a goal loop: agent works, llm judges, repeat until condition is met.

    Args:
        prompt: Task prompt for the agent
        condition: Success condition to judge
        model: Model override for both agent and judge
        effort: Reasoning effort override
        max_rounds: Max judgment rounds
        timeout_s: Timeout per agent round

    Returns:
        Final result when condition is met (or last result if max_rounds reached)
    """
    from openprogram.agentic_programming.agent import agent
    from openprogram.agentic_programming.llm import llm

    last_result = ""

    for round_num in range(max_rounds):
        # Agent works on the task
        result = agent(
            prompt=prompt,
            model=model,
            effort=effort,
            timeout_s=timeout_s,
        )
        last_result = result

        # Judge whether condition is met
        judge_prompt = f"""Judge whether this result satisfies the condition.

Condition: {condition}

Result: {result}

Reply with a JSON object:
{{"met": true/false, "reason": "brief explanation"}}"""

        judgment = llm(
            prompt=judge_prompt,
            model=model,
            effort=effort,
            response_format=_GOAL_JUDGMENT_FORMAT,
        )

        # Parse judgment
        import json
        try:
            verdict = json.loads(judgment) if isinstance(judgment, str) else judgment
            if verdict.get("met"):
                return result
        except (json.JSONDecodeError, AttributeError):
            # If judgment parsing fails, continue to next round
            continue

    # Max rounds reached without meeting condition
    return last_result


__all__ = ["goal"]
