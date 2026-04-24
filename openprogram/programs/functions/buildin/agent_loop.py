"""
agent_loop — autonomous task execution with plan-act-evaluate cycling.

For complex, long-running tasks (writing a paper, building a feature,
investigating a bug), the LLM needs to continuously plan, execute, and
re-evaluate. Python manages the loop and context; the LLM decides WHAT
to do at each step.

Architecture:
    Python controls: loop, context window, state persistence, stopping
    LLM decides: what to plan, how to act, when it's done

    Each iteration:
        1. step() — LLM sees the goal + recent history, decides next action AND executes it
        2. Python records the result as a sibling in the context tree
        3. Repeat — the next step() sees previous siblings via render_context()

    Context management:
        - Steps are siblings under a shared parent context
        - render_range={"siblings": N} gives a sliding window — recent steps,
          older steps truncated automatically
        - expose="io" folds completed steps into one-line summaries (default)
        - State is persisted to disk for crash recovery

Usage:
    from openprogram import create_runtime
    from openprogram.programs.functions.buildin.agent_loop import agent_loop

    runtime = create_runtime()

    result = agent_loop(
        goal="Write a survey paper on context management in LLM agents",
        runtime=runtime,
        max_steps=50,
    )
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.buildin._utils import parse_json
from openprogram.programs.functions.buildin.wait import wait


# ---------------------------------------------------------------------------
# Inner agentic function — one step of the loop
# ---------------------------------------------------------------------------

@agentic_function(render_range={"siblings": -1})
def _step(goal: str, step_number: int, runtime: Runtime) -> dict:
    """Autonomously advance one step toward a complex goal.

    Based on the goal and the execution history (visible in context),
    decide what to do next and do it.

    You have full freedom to:
    - Run shell commands
    - Read and write files
    - Browse the web
    - Install packages
    - Anything else you need

    Return JSON:
    {
      "done": true/false,
      "action": "what you did this step",
      "result": "outcome of the action",
      "next": "what should be done next (if not done)",
      "error": null or "error description"
    }

    Set done=true ONLY when the overall goal is fully complete.
    """
    reply = runtime.exec(content=[
        {"type": "text", "text": (
            f"Goal: {goal}\n"
            f"Step #{step_number}\n\n"
            "Decide what to do next, do it, then return JSON with "
            "done/action/result/next/error."
        )},
    ])

    try:
        return parse_json(reply)
    except ValueError:
        return {
            "done": False,
            "action": "executed",
            "result": reply[:500],
            "next": "continue",
            "error": None,
        }


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _state_path(state_dir: str, goal: str) -> str:
    """Generate a state file path from the goal."""
    # Use a hash of the goal as filename to avoid path issues
    import hashlib
    name = hashlib.sha256(goal.encode()).hexdigest()[:12]
    return os.path.join(state_dir, f"agent_loop_{name}.json")


def _load_state(path: str) -> Optional[dict]:
    """Load persisted state, or None if not found."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_state(path: str, state: dict):
    """Persist state to disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def agent_loop(
    goal: str,
    runtime: Runtime = None,
    max_steps: Optional[int] = None,
    state_dir: Optional[str] = None,
    callback: Optional[callable] = None,
) -> dict:
    """Run an autonomous agent loop for a complex goal.

    The LLM plans and executes each step. Python manages the loop,
    context window, and state persistence. Steps share context through
    the context tree — each step can see what previous steps did.

    Args:
        goal:       The high-level goal to achieve.
        runtime:    The LLM runtime to use.
        max_steps:  Max iterations before stopping (None = unlimited).
        state_dir:  Directory for state persistence (default: ~/.agentic/logs/).
                    Set to enable crash recovery — the loop resumes from
                    the last completed step.
        callback:   Called after each step with the step result dict.
                    Return False to stop the loop early.

    Returns:
        dict with keys: done, steps, history, error
    """
    if runtime is None:
        raise ValueError("runtime is required for agent_loop()")

    # State persistence
    if state_dir is None:
        from openprogram.paths import get_logs_dir
        state_dir = str(get_logs_dir())
    sp = _state_path(state_dir, goal)
    state = _load_state(sp) or {"goal": goal, "steps": 0, "history": [], "done": False}

    step_num = state["steps"]

    while not state["done"]:
        if max_steps is not None and step_num >= max_steps:
            state["error"] = f"max_steps ({max_steps}) reached"
            break

        step_num += 1
        try:
            result = _step(goal=goal, step_number=step_num, runtime=runtime)
        except Exception as e:
            result = {
                "done": False,
                "action": "error",
                "result": None,
                "next": "retry or adjust",
                "error": f"{type(e).__name__}: {e}",
            }

        state["steps"] = step_num
        state["history"].append({
            "step": step_num,
            "timestamp": time.time(),
            **result,
        })
        state["done"] = bool(result.get("done"))
        _save_state(sp, state)

        if callback is not None:
            if callback(result) is False:
                state["done"] = True
                state["cancelled"] = True
                _save_state(sp, state)
                break

        # Adaptive waiting — LLM decides how long to wait
        if not state["done"]:
            action_desc = result.get("action", "completed a step")
            wait(action=action_desc, runtime=runtime)

    return state
