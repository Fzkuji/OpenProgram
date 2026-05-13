"""
create() — Generate a single @agentic_function from a natural language description.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.meta.generation.clarify import clarify
from openprogram.programs.functions.meta.validation.compile_function import compile_function
from openprogram.programs.functions.meta.generation.extract_code import extract_code
from openprogram.programs.functions.meta.generation.generate_code import generate_code
from openprogram.programs.functions.meta.validation.validate_code import validate_code


_DEFAULT_SAVE_DIR = (
    Path(__file__).resolve().parent.parent / "third_party"
)


def _resolve_save_path(save_to: str | None, fn_name: str) -> Path:
    """Return the .py path to write generated code to.

    Rules:
      - If `save_to` is a full filename ending in .py, use it directly.
      - If `save_to` is a directory, append `<fn_name>.py`.
      - If `save_to` is None, fall back to the framework default
        `openprogram/programs/functions/third_party/<fn_name>.py`.
    """
    if save_to:
        p = Path(save_to).expanduser()
        if p.suffix == ".py":
            return p
        return p / f"{fn_name}.py"
    return _DEFAULT_SAVE_DIR / f"{fn_name}.py"


def _guess_name(code: str) -> str | None:
    """Return the function name from generated code (prefer @agentic_function-decorated)."""
    match = re.search(r"@agentic_function[^\n]*\s*def\s+(\w+)\s*\(", code)
    if match:
        return match.group(1)
    match = re.search(r"def\s+(\w+)\s*\(", code)
    return match.group(1) if match else None


@agentic_function(input={
    "description": {
        "description": "What the function should do",
        "placeholder": "e.g. count words in a text string",
        "multiline": True,
    },
    "runtime": {"hidden": True},
    "name": {
        "description": "Function name (LLM is told to use this; falls back to LLM-chosen name)",
        "placeholder": "e.g. my_function",
        "multiline": False,
    },
    "save_to": {
        "description": "Where to save the generated .py file. May be a full file path or a directory. Defaults to programs/functions/third_party/<name>.py.",
        "placeholder": "e.g. /path/to/dir or /path/to/dir/my_function.py",
        "multiline": False,
    },
})
def create(
    description: str,
    runtime: Runtime,
    name: str = None,
    save_to: str = None,
):
    """Create a new Python function from a natural language description.

    Calls generate_code() with the design specification, then extracts,
    validates, compiles, and saves the generated code.

    Args:
        description: What the function should do.
        runtime: Runtime instance for LLM calls.
        name: Optional name. Included in the task so the LLM names the
              function this way; if the LLM disregards it the file is
              still saved under whatever name appears in the code.
        save_to: Where to save. Full file path or directory. When None,
              defaults to ``programs/functions/third_party/<name>.py``.

    Returns:
        callable — the generated function, or
        dict — {"type": "follow_up", "question": "..."} if LLM needs more info.
    """
    task = (
        f"Write a Python function that does the following:\n\n"
        f"{description}"
    )
    if name:
        task += f"\n\nName the function exactly `{name}`."
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
    fn_name = _guess_name(code) or name or "generated"

    validate_code(code, response)

    # Step 3: Save to disk (caller-chosen path or framework default).
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        target = _resolve_save_path(save_to, fn_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code)

    return compile_function(code, runtime, fn_name)
