"""
fix() — Analyze and rewrite an existing function based on errors and instructions.
"""

from __future__ import annotations

from typing import Callable, Optional

from agentic.function import agentic_function
from agentic.runtime import Runtime
from agentic.meta_functions._helpers import (
    extract_code, validate_code, compile_function,
    save_function, get_source, get_error_log,
)


@agentic_function
def fix(
    fn,
    runtime: Runtime,
    instruction: str = None,
    name: str = None,
    on_question: Callable[[str], str] = None,
    max_rounds: int = 5,
) -> callable:
    """Rewrite and fix the given function based on its code, errors, and optional instruction.

    Rules for the rewritten function:
    - If it needs LLM reasoning, use @agentic_function + runtime.exec().
      Content is a list of dicts: [{"type": "text", "text": "..."}].
      `agentic_function` and `runtime` are already available in scope.
    - If purely deterministic, write a normal function.
    - Type hints, Google-style docstring, standard library imports allowed.
    - No async/await.
    - The fixed function will overwrite the original in agentic/functions/.
    - Fix the root cause, not just the symptom. If the error is a format mismatch,
      make the format explicit in the docstring. If it's a type error, add validation.
    - If the error involves text input/typing, handle special characters and edge cases.
    - If output format matters, define it precisely in the docstring with examples.

    If unsure about what to fix, respond with ONLY "QUESTION: <your question>".
    Otherwise, respond with ONLY the fixed function definition (no explanation).

    Args:
        fn:           The function to fix.
        runtime:      Runtime instance for LLM calls.
        instruction:  Optional manual instruction ("change X to Y").
        name:         Optional name override.
        on_question:  Callback for interactive fixing. fn(question) -> answer.
        max_rounds:   Maximum interaction rounds (default 5).

    Returns:
        A new callable function with fixes applied.
    """
    # Auto-extract everything from fn
    description = getattr(fn, '__doc__', '') or getattr(fn, '__name__', 'unknown')
    code = get_source(fn)
    error_log = get_error_log(fn)
    fn_name = name or getattr(fn, '__name__', 'fixed')

    # Build data for LLM (rules are in docstring, only pass data here)
    data_parts = [f"Current code:\n```python\n{code}\n```"]
    if error_log:
        data_parts.append(f"Error log:\n{error_log}")
    if instruction:
        data_parts.append(f"Instruction: {instruction}")

    # Interaction loop
    extra_context = ""
    for round_num in range(max_rounds):
        prompt = "\n\n".join(data_parts)
        if extra_context:
            prompt += extra_context

        # Only the first round uses runtime.exec() (one exec per agentic_function)
        # Subsequent rounds use runtime._call() directly since we need multiple LLM calls
        if round_num == 0:
            response = runtime.exec(content=[
                {"type": "text", "text": prompt},
            ])
        else:
            response = runtime._call(
                [{"type": "text", "text": prompt}],
                model=runtime.model,
            )

        # Check if LLM is asking a question
        if response.strip().startswith("QUESTION:"):
            question = response.strip()[len("QUESTION:"):].strip()
            if on_question is None:
                extra_context += f"\nNote: You cannot ask questions. Produce the fixed code directly.\n"
                continue
            else:
                answer = on_question(question)
                extra_context += f"\nQ: {question}\nA: {answer}\n"
                continue

        # Got code — save, validate, compile
        fixed_code = extract_code(response)
        save_function(fixed_code, fn_name, f"Fixed: {description}")
        validate_code(fixed_code, response)
        return compile_function(fixed_code, runtime, fn_name)

    raise RuntimeError(f"fix() exceeded max_rounds ({max_rounds}) without producing valid code.")
