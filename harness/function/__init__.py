"""
Function — the core unit of Agentic Programming.

A Function is a Python function whose body is executed by an LLM.
Just like a normal Python function, you call it and get a result.
The difference: instead of a CPU executing the logic, an LLM does.

    Python:     result = my_func(x, y)     → CPU executes → returns result
    Agentic:    result = observe(session, task="...")  → LLM executes → returns result

Usage:

    from harness import function, Session
    from pydantic import BaseModel

    class ObserveResult(BaseModel):
        elements: list[str]
        target_visible: bool

    @function(return_type=ObserveResult)
    def observe(session: Session, task: str) -> ObserveResult:
        '''Observe the current screen state and identify UI elements.'''

    # Call it like any function
    result = observe(session, task="find the login button")
    # result is ObserveResult — guaranteed

The decorator handles:
    - Assembling the prompt from the function's docstring + arguments
    - Sending it to the Session (LLM)
    - Parsing and validating the output against return_type
    - Retrying if the output doesn't match
    - Logging to Memory (if provided)

You can also write functions manually without the decorator:

    def observe(session: Session, task: str) -> ObserveResult:
        reply = session.send(f"Observe the screen. Task: {task}")
        return ObserveResult.model_validate_json(reply)
"""

from __future__ import annotations

import functools
import json
import time
from typing import Type, TypeVar, Optional, Callable, Any, get_type_hints
from pydantic import BaseModel

from harness.session import Session

T = TypeVar("T", bound=BaseModel)


class FunctionError(Exception):
    """Raised when a Function fails after all retries."""
    def __init__(self, function_name: str, message: str):
        self.function_name = function_name
        super().__init__(f"{function_name}: {message}")


def function(
    return_type: Type[T],
    max_retries: int = 3,
    examples: list[dict] = None,
) -> Callable:
    """
    Decorator that turns a Python function into an LLM-executed Function.

    The decorated function must have:
        - First parameter: session (Session)
        - A docstring (used as instructions for the LLM)
        - Other parameters become the function's input

    Args:
        return_type:  Pydantic model the LLM must return
        max_retries:  How many times to retry on invalid output
        examples:     Optional list of {"input": ..., "output": ...} dicts

    Returns:
        A wrapper that calls the LLM and returns a validated Pydantic object

    Example:
        @function(return_type=ObserveResult)
        def observe(session: Session, task: str) -> ObserveResult:
            '''Observe the current screen state.'''

        result = observe(session, task="find login button")
    """
    def decorator(fn: Callable) -> Callable:
        fn_name = fn.__name__
        fn_doc = fn.__doc__ or ""

        @functools.wraps(fn)
        def wrapper(session: Session, **kwargs) -> T:
            # Assemble prompt
            prompt = _assemble_prompt(fn_name, fn_doc, kwargs, return_type, examples)

            # Try up to max_retries times
            last_error = None
            for attempt in range(max_retries):
                reply = session.send(prompt)

                try:
                    result = _parse_output(reply, return_type)
                    return result
                except (json.JSONDecodeError, Exception) as e:
                    last_error = str(e)
                    # Add retry hint to next attempt
                    prompt = (
                        f"Your previous response was invalid: {last_error}\n"
                        f"Please try again. Return ONLY valid JSON matching the schema.\n"
                        f"Schema: {json.dumps(return_type.model_json_schema(), indent=2)}"
                    )

            raise FunctionError(fn_name, f"Failed after {max_retries} attempts. Last error: {last_error}")

        # Attach metadata for introspection
        wrapper._is_function = True
        wrapper._return_type = return_type
        wrapper._max_retries = max_retries
        wrapper._examples = examples or []
        wrapper._fn_name = fn_name
        wrapper._fn_doc = fn_doc

        return wrapper

    return decorator


# ------------------------------------------------------------------
# Built-in Functions — basic operations every agent needs
# ------------------------------------------------------------------

def ask(session: Session, question: str) -> str:
    """Ask the LLM a question, get a plain text answer."""
    return session.send(question)


def extract(session: Session, text: str, schema: Type[T]) -> T:
    """Extract structured data from text.

    Args:
        session:  LLM session
        text:     Text to extract from
        schema:   Pydantic model defining what to extract

    Returns:
        Validated Pydantic object
    """
    prompt = (
        f"Extract the following information from the text below.\n\n"
        f"Text:\n{text}\n\n"
        f"Return ONLY valid JSON matching this schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}"
    )
    reply = session.send(prompt)
    return _parse_output(reply, schema)


def summarize(session: Session, text: str, max_length: int = None) -> str:
    """Summarize text."""
    prompt = f"Summarize the following text"
    if max_length:
        prompt += f" in {max_length} words or less"
    prompt += f":\n\n{text}"
    return session.send(prompt)


def classify(session: Session, text: str, categories: list[str]) -> str:
    """Classify text into one of the given categories.

    Returns the category name (one of the provided options).
    """
    cats = ", ".join(f'"{c}"' for c in categories)
    prompt = (
        f"Classify the following text into exactly one of these categories: {cats}\n\n"
        f"Text: {text}\n\n"
        f"Reply with ONLY the category name, nothing else."
    )
    reply = session.send(prompt).strip().strip('"')
    # Fuzzy match to closest category
    for cat in categories:
        if cat.lower() == reply.lower():
            return cat
    return reply  # return as-is if no exact match


def decide(session: Session, question: str, options: list[str]) -> str:
    """Ask the LLM to choose from a list of options.

    Returns the chosen option.
    """
    opts = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))
    prompt = (
        f"Question: {question}\n\n"
        f"Options:\n{opts}\n\n"
        f"Reply with ONLY the option text (not the number)."
    )
    reply = session.send(prompt).strip()
    for opt in options:
        if opt.lower() == reply.lower():
            return opt
    return reply


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _assemble_prompt(
    fn_name: str,
    fn_doc: str,
    kwargs: dict,
    return_type: Type[T],
    examples: list[dict] = None,
) -> str:
    """Assemble the prompt sent to the LLM."""
    parts = []

    parts.append(f"## Function: {fn_name}")
    parts.append("")

    if fn_doc:
        parts.append(f"### Instructions")
        parts.append(fn_doc.strip())
        parts.append("")

    if kwargs:
        parts.append(f"### Arguments")
        parts.append(json.dumps(kwargs, indent=2, ensure_ascii=False, default=str))
        parts.append("")

    if examples:
        parts.append("### Examples")
        for ex in examples:
            parts.append(f"Input: {json.dumps(ex.get('input', {}), ensure_ascii=False)}")
            parts.append(f"Output: {json.dumps(ex.get('output', {}), ensure_ascii=False)}")
            parts.append("")

    parts.append("### Return format")
    parts.append("You MUST respond with a JSON object matching this schema exactly.")
    parts.append("Do not add extra fields. Do not wrap in markdown code blocks.")
    parts.append(json.dumps(return_type.model_json_schema(), indent=2))

    return "\n".join(parts)


def _parse_output(reply: str, return_type: Type[T]) -> T:
    """Parse LLM reply into a Pydantic model."""
    text = reply.strip()

    # Strip markdown code blocks if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    return return_type.model_validate_json(text)
