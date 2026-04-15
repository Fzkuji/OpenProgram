"""
improve() — Optimize an existing function based on a goal.
"""

from __future__ import annotations

from agentic.function import agentic_function
from agentic.runtime import Runtime
from agentic.meta_functions._helpers import (
    extract_code, validate_code, compile_function,
    save_function, get_source,
    _canonicalize_function_code,
    clarify, generate_code,
)


@agentic_function(input={
    "fn": {
        "description": "Function to improve",
        "options_from": "functions",
        "multiline": False,
    },
    "runtime": {"hidden": True},
    "goal": {
        "description": "Improvement goal",
        "placeholder": "e.g. better prompt, more robust, cleaner code",
        "multiline": True,
    },
    "name": {
        "description": "Rename the improved function",
        "placeholder": "e.g. sentiment_v2",
        "multiline": False,
    },
})
def improve(
    fn,
    runtime: Runtime,
    goal: str = "general improvement",
    name: str = None,
):
    """Improve an existing function based on a specified goal.

    Calls generate_code() with the current code and improvement goal,
    then extracts, validates, compiles, and saves the improved code.

    Args:
        fn: The function to improve.
        runtime: Runtime instance for LLM calls.
        goal: What to improve (e.g., "better prompt", "more robust", "cleaner code").
        name: Optional name override.

    Returns:
        callable — the improved function, or
        dict — {"type": "follow_up", "question": "..."} if LLM needs more info.
    """
    import inspect as _inspect

    code = get_source(fn)
    fn_name = name or getattr(fn, '__name__', 'improved')

    # Resolve file path for context
    try:
        _inner = getattr(fn, '__wrapped__', fn)
        fn_filepath = _inspect.getfile(_inner)
    except (TypeError, OSError):
        fn_filepath = None

    header = f"Function: {fn_name}"
    if fn_filepath:
        header += f"\nFile: {fn_filepath}"

    task = (
        f"{header}\n\n"
        f"Improve the following function:\n\n"
        f"```python\n{code}\n```\n\n"
        f"Improvement goal: {goal}"
    )
    generation_task = (
        f"{task}\n\n"
        f"Respond with ONLY the improved Python code in a ```python fence. "
        f"No explanation, no commentary."
    )
    # Step 1: Clarify — enough info?
    check = clarify(task=task, runtime=runtime)
    if not check.get("ready", True):
        return {"type": "follow_up", "question": check.get("question", "Need more information.")}

    # Step 2: Generate code
    response = generate_code(task=generation_task, runtime=runtime)
    improved_code = extract_code(response)
    improved_code = _canonicalize_function_code(improved_code, fn_name)
    save_function(
        improved_code,
        fn_name,
        f"Improved: {goal}",
        source_path=fn_filepath,
        action="improve",
    )
    validate_code(improved_code, response)
    return compile_function(improved_code, runtime, fn_name)
