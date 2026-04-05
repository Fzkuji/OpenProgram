"""
create() — Generate a single @agentic_function from a natural language description.
"""

from __future__ import annotations

from agentic.function import agentic_function
from agentic.runtime import Runtime
from agentic.meta_functions._helpers import (
    extract_code, validate_code, compile_function,
    save_function, save_skill_template, guess_name,
)


@agentic_function
def create(description: str, runtime: Runtime, name: str = None, as_skill: bool = False) -> callable:
    """Write a Python function based on the user's description.

    IMPORTANT — How our framework works:
    In Agentic Programming, the function's docstring IS the LLM prompt.
    When runtime.exec() is called, the framework automatically sends:
    1. The full execution context (parent functions, sibling results)
    2. The current function's docstring
    3. The current function's parameters and their values
    4. Whatever the function passes in content=[...]

    So the docstring already tells the LLM what to do. The content should
    ONLY contain the actual data (user's text, file path, etc.), NOT
    repeated instructions. The docstring handles the instructions.

    Rules:
    - If the task requires LLM reasoning, use @agentic_function + runtime.exec().
      `agentic_function` and `runtime` are already available in scope.
    - If the task is purely deterministic, write a normal Python function
      WITHOUT @agentic_function and WITHOUT runtime.exec().
    - Standard library imports allowed (os, json, re, pathlib, math, etc.).
    - No async/await.
    - Type hints on all parameters and return type.
    - Google-style docstring: one-line summary, Args, Returns.
    - The function will be saved to agentic/functions/ for reuse.

    Example of a CORRECT agentic function:

        @agentic_function
        def sentiment(text: str) -> str:
            \"\"\"Analyze the sentiment of the given text.
            Return exactly one word: positive, negative, or neutral.\"\"\"\n            return runtime.exec(content=[
                {"type": "text", "text": text},
            ])

    Notice: the docstring says what to do ("analyze sentiment, return one word").
    The content ONLY passes the data (text). No instructions in content.

    Example of a CORRECT pure Python function:

        def word_count(text: str) -> int:
            \"\"\"Count the number of words in a text string.

            Args:
                text: The input text.

            Returns:
                Number of words.
            \"\"\"\n            return len(text.split())

    Robustness rules:
    - If the task has a specific output format, define it precisely in the
      docstring (e.g., "Return format: 'Lec 2 (12:00-14:00)'"). Don't let
      the LLM guess the format.
    - If the function involves text input/typing, handle special characters,
      escaping, and edge cases (empty input, very long input, etc.).
    - If the function depends on external state (files, UI, APIs), validate
      inputs and handle errors with clear messages.
    - Prefer returning structured data (dict/JSON) over free-form text when
      the result will be used by other functions.
    - If exact formatting matters, include an example in the docstring:
      e.g., "Example output: {'time': '12:00-14:00', 'type': 'Lecture'}"

    Write ONLY the code needed for the function. No explanation.
    Imports are allowed only when genuinely needed, and they must stay within
    the safe standard-library whitelist.

    Args:
        description:  What the function should do.
        runtime:      Runtime instance for LLM calls.
        name:         Optional name override.
        as_skill:     If True, also create a SKILL.md for agent discovery.

    Returns:
        A callable function.
    """
    response = runtime.exec(content=[
        {"type": "text", "text": f"Write a Python function that does the following:\n\n{description}"},
    ])
    code = extract_code(response)
    fn_name = name or guess_name(code) or "generated"

    # Save first, then validate and compile
    save_function(code, fn_name, description)
    if as_skill:
        save_skill_template(fn_name, description, code)
    validate_code(code, response)
    return compile_function(code, runtime, name)
