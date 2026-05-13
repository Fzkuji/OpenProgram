"""
create() — Generate a single @agentic_function from a natural language description.
"""

from __future__ import annotations

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.meta._helpers import (
    extract_code, validate_code, compile_function,
    save_function, save_skill_template, guess_name,
    _canonicalize_function_code,
    clarify, generate_code,
)


@agentic_function(input={
    "description": {
        "description": "What the function should do",
        "placeholder": "e.g. count words in a text string",
        "multiline": True,
    },
    "runtime": {"hidden": True},
    "name": {
        "description": "Function name override",
        "placeholder": "e.g. my_function",
        "multiline": False,
    },
    "as_skill": {
        "description": "Also create a SKILL.md",
    },
})
def create(description: str, runtime: Runtime, name: str = None, as_skill: bool = False):
    """Create a new Python function from a natural language description.

    Calls generate_code() with the design specification, then extracts,
    validates, compiles, and saves the generated code.

    Args:
        description: What the function should do.
        runtime: Runtime instance for LLM calls.
        name: Optional name override.
        as_skill: If True, also create a SKILL.md for agent discovery.

    Returns:
        callable — the generated function, or
        dict — {"type": "follow_up", "question": "..."} if LLM needs more info.
    """
    task = (
        f"Write a Python function that does the following:\n\n"
        f"{description}"
    )
    generation_task = (
        f"{task}\n\n"
        f"Respond with ONLY the Python code inside a ```python code fence. "
        f"No explanation, no commentary, no markdown outside the fence."
    )

    # Step 1: Clarify — only run if we have a way to ask the user.
    # In headless / subprocess mode no handler is registered, so clarify's
    # follow-up question would just bounce off ask_user → None → abort.
    # Skip it entirely and let generate_code fill in sensible defaults.
    from openprogram.programs.functions.buildin.ask_user import (
        ask_user, has_ask_user_handler,
    )
    if has_ask_user_handler():
        check = clarify(task=task, runtime=runtime)
        if not check.get("ready", True):
            question = check.get("question", "Need more information.")
            answer = ask_user(question)
            if answer and answer.strip():
                task += f"\n\nUser clarification: {answer}"
                generation_task = (
                    f"{task}\n\n"
                    f"Respond with ONLY the Python code inside a ```python code fence. "
                    f"No explanation, no commentary, no markdown outside the fence."
                )
            else:
                return {"type": "follow_up", "question": question}

    # Step 2: Generate code
    response = generate_code(task=generation_task, runtime=runtime)
    code = extract_code(response)
    fn_name = name or guess_name(code) or "generated"
    code = _canonicalize_function_code(code, fn_name)

    save_function(code, fn_name, description)
    if as_skill:
        save_skill_template(fn_name, description, code)
    validate_code(code, response)
    return compile_function(code, runtime, fn_name)
