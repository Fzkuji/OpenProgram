"""
evaluate — prompt competition via competing @agentic_functions.

Instead of external prompt files, each competing approach is an
@agentic_function whose docstring IS the prompt. The evaluator
runs each function on the same input, then uses a (potentially
different) LLM to pick the best output.

Usage:
    from openprogram.functions.agentics.research.evaluate import compete

    best = compete(
        functions=[polish_v1, polish_v2],
        kwargs={"text": "We propose ...", "runtime": exec_runtime},
        eval_runtime=gpt_runtime,
        task="Polish academic LaTeX for NeurIPS",
    )
"""

from __future__ import annotations

from typing import Optional

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.functions.agentics._utils import parse_json


@agentic_function(render_range={"callers": 0})
def _evaluate_candidates(task: str, candidates: list[dict],
                         runtime: Runtime) -> dict:
    """Pick the best candidate output for an academic writing task.
    Score each on: accuracy, quality, academic rigor, naturalness.
    Return JSON: {"winner": <1-based>, "scores": [...], "reasoning": "..."}"""
    parts = [f"Task: {task}\n"]
    for i, c in enumerate(candidates):
        parts.append(f"--- Candidate {i+1} ({c['name']}) ---")
        parts.append(c["output"][:3000])

    return runtime.exec(content=[
        {"type": "text", "text": "\n".join(parts)},
    ])


def compete(
    functions: list[callable],
    kwargs: dict,
    eval_runtime: Runtime,
    task: str = "Pick the best academic writing output",
) -> dict:
    """Run prompt competition between @agentic_functions.

    Each function is called with the same kwargs. Their outputs are
    evaluated by eval_runtime (ideally a different model).

    Args:
        functions:    List of @agentic_function callables to compete.
        kwargs:       Keyword arguments to pass to each function.
        eval_runtime: Runtime for evaluation (different model recommended).
        task:         Description of what we're evaluating.

    Returns:
        dict with: winner_index, winner_output, winner_name,
                   scores, reasoning, all_candidates
    """
    if len(functions) == 1:
        output = functions[0](**kwargs)
        return {
            "winner_index": 0,
            "winner_output": output,
            "winner_name": functions[0].__name__,
            "scores": [10],
            "reasoning": "Single candidate",
            "all_candidates": [{"name": functions[0].__name__, "output": output}],
        }

    # Generate from each function
    candidates = []
    for fn in functions:
        output = fn(**kwargs)
        candidates.append({"name": fn.__name__, "output": str(output)})

    # Evaluate
    reply = _evaluate_candidates(
        task=task, candidates=candidates, runtime=eval_runtime,
    )

    try:
        result = parse_json(reply)
    except ValueError:
        result = {"winner": 1, "scores": [5] * len(candidates), "reasoning": reply[:200]}

    idx = result.get("winner", 1) - 1
    idx = max(0, min(idx, len(candidates) - 1))

    return {
        "winner_index": idx,
        "winner_output": candidates[idx]["output"],
        "winner_name": candidates[idx]["name"],
        "scores": result.get("scores", []),
        "reasoning": result.get("reasoning", ""),
        "all_candidates": candidates,
    }
