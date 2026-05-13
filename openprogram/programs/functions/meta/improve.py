"""
improve() — Optimize an existing function based on a goal.
"""

from __future__ import annotations

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.meta.generation.clarify import clarify
from openprogram.programs.functions.meta.validation.compile_function import compile_function
from openprogram.programs.functions.meta.generation.extract_code import extract_code
from openprogram.programs.functions.meta.generation.generate_code import generate_code
from openprogram.programs.functions.meta.introspection.get_source import get_source
from openprogram.programs.functions.meta.storage.save_function import save_function
from openprogram.programs.functions.meta.validation.validate_code import validate_code


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
        from openprogram.programs.functions.buildin.ask_user import ask_user
        question = check.get("question", "Need more information.")
        answer = ask_user(question)
        if answer and answer.strip():
            task += f"\n\nUser clarification: {answer}"
            generation_task = (
                f"{task}\n\n"
                f"Respond with ONLY the improved Python code in a ```python fence. "
                f"No explanation, no commentary."
            )
        else:
            return {"type": "follow_up", "question": question}

    # Step 2: Generate code
    response = generate_code(task=generation_task, runtime=runtime)
    improved_code = extract_code(response)
    save_function(
        improved_code,
        fn_name,
        f"Improved: {goal}",
        source_path=fn_filepath,
        action="improve",
    )
    validate_code(improved_code, response)
    return compile_function(improved_code, runtime, fn_name)
