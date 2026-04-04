"""
meta_function — Generate and fix agentic functions from natural language.

Two primitives:
    create()  — LLM writes a new @agentic_function from a description
    fix()     — LLM analyzes and rewrites an existing function

Usage:
    from agentic import Runtime
    from agentic.meta_function import create, fix

    runtime = Runtime(call=my_llm, model="sonnet")

    # Create a function from scratch
    summarize = create("Summarize text into 3 bullet points", runtime=runtime)

    # Fix a function (auto-detects code + errors from context)
    fixed = fix(fn=summarize, runtime=runtime)

    # Fix with manual instruction
    fixed = fix(fn=summarize, runtime=runtime, instruction="Use bullet points, not numbered list")
"""

from __future__ import annotations

import inspect
import re
from typing import Callable, Optional

from agentic.function import agentic_function
from agentic.runtime import Runtime


# ── Prompts ─────────────────────────────────────────────────────

_GENERATE_PROMPT = """\
Write a Python function that does the following:

{description}

Rules:
1. Decorate with @agentic_function
2. Write a clear docstring — this becomes the LLM prompt
3. Use runtime.exec() to call the LLM when reasoning is needed
4. Content is a list of dicts: [{{"type": "text", "text": "..."}}]
5. Return a meaningful result (string or dict)
6. Use only standard Python — no imports needed
7. Do NOT use async/await — write a normal synchronous function

`agentic_function` and `runtime` are already available in scope.

Write ONLY the function definition. No imports, no examples, no explanation.
Start with @agentic_function and end with the return statement.
"""

_FIX_PROMPT = """\
Fix the following agentic function.

Function description: {description}

Current code:
```python
{code}
```
{error_section}
{instruction_section}
Rewrite the function to fix the issues.

Rules:
1. Decorate with @agentic_function
2. Write a clear docstring
3. Use runtime.exec() to call the LLM when reasoning is needed
4. Content is a list of dicts: [{{"type": "text", "text": "..."}}]
5. Return a meaningful result (string or dict)
6. Use only standard Python — no imports needed
7. Do NOT use async/await

`agentic_function` and `runtime` are already available in scope.

If you are unsure about anything and need clarification, respond with
ONLY a question starting with "QUESTION:" (no code). Otherwise, respond
with ONLY the fixed function definition (no explanation).
"""


# ── Safety ──────────────────────────────────────────────────────

_ALLOWED_BUILTINS = {
    "abs", "all", "any", "bool", "chr", "dict", "dir", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "hasattr",
    "hash", "hex", "id", "int", "isinstance", "issubclass", "iter",
    "len", "list", "map", "max", "min", "next", "oct", "ord", "pow",
    "print", "range", "repr", "reversed", "round", "set", "slice",
    "sorted", "str", "sum", "tuple", "type", "zip",
    "True", "False", "None", "ValueError", "TypeError", "KeyError",
    "IndexError", "RuntimeError", "Exception",
}


def _make_safe_builtins() -> dict:
    """Create a restricted builtins dict."""
    import builtins
    safe = {}
    for name in _ALLOWED_BUILTINS:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    safe["__import__"] = _blocked_import
    return safe


def _blocked_import(name, *args, **kwargs):
    raise ImportError(
        f"Import '{name}' is not allowed in generated functions. "
        f"Use runtime.exec() for any task that needs external libraries."
    )


# ── Internal helpers ────────────────────────────────────────────

def _extract_code(response: str) -> str:
    """Extract Python code from LLM response, stripping markdown fences."""
    match = re.search(r"```(?:python)?\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"(@agentic_function.*)", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()


def _validate_code(code: str, response: str) -> None:
    """Validate generated code: no imports, no async, valid syntax."""
    for line in (response + "\n" + code).split("\n"):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            raise ValueError(
                f"Generated code contains import statements (not allowed):\n{code}"
            )
        if stripped.startswith("async def ") or stripped.startswith("async "):
            raise ValueError(
                f"Generated code uses async (not allowed, use sync functions):\n{code}"
            )
    try:
        compile(code, "<generated>", "exec")
    except SyntaxError as e:
        raise SyntaxError(
            f"Generated code has syntax errors:\n{code}\n\nError: {e}"
        ) from e


def _compile_function(code: str, runtime: Runtime, name: str = None) -> callable:
    """Execute code in sandbox and return the generated agentic_function."""
    namespace = {
        "__builtins__": _make_safe_builtins(),
        "agentic_function": agentic_function,
        "runtime": runtime,
    }
    try:
        exec(code, namespace)
    except Exception as e:
        raise ValueError(
            f"Generated code failed to execute:\n{code}\n\nError: {e}"
        ) from e

    fn = _find_function(namespace)
    if fn is None:
        raise ValueError(
            f"Generated code does not contain an @agentic_function:\n{code}"
        )
    if name:
        fn.__name__ = name
        fn.__qualname__ = name
    return fn


def _find_function(namespace: dict) -> Optional[callable]:
    """Find the generated agentic_function in the namespace."""
    for obj_name, obj in namespace.items():
        if obj_name.startswith("_"):
            continue
        if isinstance(obj, agentic_function):
            return obj
    return None


def _get_source(fn) -> str:
    """Get source code of a function. Falls back to docstring if unavailable."""
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        doc = getattr(fn, '__doc__', '') or ''
        name = getattr(fn, '__name__', 'unknown')
        return f"# Source not available for {name}\n# Docstring: {doc}"


def _get_error_log(fn) -> str:
    """Build error log from function's Context (attempts + errors)."""
    ctx = getattr(fn, 'context', None)
    if ctx is None:
        return ""

    lines = []
    _collect_attempt_info(ctx, lines)
    return "\n".join(lines) if lines else ""


def _collect_attempt_info(ctx, lines: list, depth: int = 0):
    """Recursively collect attempt info from Context tree."""
    prefix = "  " * depth
    if ctx.attempts:
        for a in ctx.attempts:
            status = "OK" if a["error"] is None else "FAILED"
            lines.append(f"{prefix}{ctx.name} attempt {a['attempt']}: {status}")
            if a["error"]:
                lines.append(f"{prefix}  Error: {a['error']}")
            if a.get("reply") and a["error"]:
                lines.append(f"{prefix}  Reply was: {str(a['reply'])[:300]}")
    elif ctx.error:
        lines.append(f"{prefix}{ctx.name}: error: {ctx.error}")
    for child in ctx.children:
        _collect_attempt_info(child, lines, depth + 1)


# ── Core: create() ──────────────────────────────────────────────

@agentic_function
def create(description: str, runtime: Runtime, name: str = None) -> callable:
    """Create a new @agentic_function from a natural language description.

    Args:
        description:  What the function should do.
        runtime:      Runtime instance for LLM calls.
        name:         Optional name override for the generated function.

    Returns:
        A callable @agentic_function ready to use.
    """
    response = runtime.exec(content=[
        {"type": "text", "text": _GENERATE_PROMPT.format(description=description)},
    ])
    code = _extract_code(response)
    _validate_code(code, response)
    return _compile_function(code, runtime, name)


# ── Core: fix() ─────────────────────────────────────────────────

@agentic_function
def fix(
    fn,
    runtime: Runtime,
    instruction: str = None,
    name: str = None,
    on_question: Callable[[str], str] = None,
    max_rounds: int = 5,
) -> callable:
    """Fix an existing @agentic_function by letting LLM analyze and rewrite it.

    Automatically extracts source code, docstring, and error context from fn.
    Supports interactive mode: if LLM has questions, it calls on_question.

    Args:
        fn:           The function to fix.
        runtime:      Runtime instance for LLM calls.
        instruction:  Optional manual instruction ("change X to Y").
                      If None, LLM auto-analyzes errors from fn.context.
        name:         Optional name override for the fixed function.
        on_question:  Optional callback for interactive fixing.
                      Signature: fn(question: str) -> str (your answer).
                      If None, LLM must produce code without asking.
        max_rounds:   Maximum interaction rounds (default 5).

    Returns:
        A new callable @agentic_function with fixes applied.
    """
    # Auto-extract everything from fn
    description = getattr(fn, '__doc__', '') or getattr(fn, '__name__', 'unknown')
    code = _get_source(fn)
    error_log = _get_error_log(fn)
    fn_name = name or getattr(fn, '__name__', 'fixed')

    # Build prompt sections
    error_section = ""
    if error_log:
        error_section = f"\nError log from previous execution:\n{error_log}\n"

    instruction_section = ""
    if instruction:
        instruction_section = f"\nUser instruction: {instruction}\n"

    # Interaction loop
    extra_context = ""
    for round_num in range(max_rounds):
        prompt = _FIX_PROMPT.format(
            description=description,
            code=code,
            error_section=error_section,
            instruction_section=instruction_section + extra_context,
        )

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
                # No callback — LLM must produce code, retry without question
                extra_context += f"\nNote: You cannot ask questions. Produce the fixed code directly.\n"
                continue
            else:
                answer = on_question(question)
                extra_context += f"\nQ: {question}\nA: {answer}\n"
                continue

        # Got code — extract, validate, compile
        fixed_code = _extract_code(response)
        _validate_code(fixed_code, response)
        return _compile_function(fixed_code, runtime, fn_name)

    raise RuntimeError(f"fix() exceeded max_rounds ({max_rounds}) without producing valid code.")
