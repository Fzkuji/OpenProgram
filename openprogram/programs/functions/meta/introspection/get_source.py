"""get_source / get_error_log — fetch source code and prior attempt info
for a function, used by the edit / fix / improve flows.
"""

from __future__ import annotations

import inspect
import re


def get_source(fn) -> str:
    """Get source code of a function.

    Supports three cases:
      1. Normal function — ``inspect.getsource()``.
      2. ``_FunctionStub`` (broken module) — ``__source__`` carries the full file.
      3. Fallback — docstring or placeholder.
    """
    # Plain string means the function couldn't be loaded — let the LLM know.
    if isinstance(fn, str):
        return f"# Function '{fn}' not found — no source code available."

    source_attr = getattr(fn, '__source__', None)
    if source_attr:
        return source_attr

    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        doc = getattr(fn, '__doc__', '') or ''
        name = getattr(fn, '__name__', 'unknown')
        if (
            inspect.isbuiltin(fn)
            or getattr(fn, "__module__", None) == "builtins"
            or _looks_like_api_doc(doc)
        ):
            return f"# Source not available for {name}"
        return f"# Source not available for {name}\n# Docstring: {doc}"


def _looks_like_api_doc(doc: str) -> bool:
    """Heuristically detect built-in/API reference docstrings.

    When source is unavailable, some callables expose long reference-style
    docstrings that are useful as API docs but noisy as prompt input.
    """
    if not doc:
        return False

    first_line = doc.strip().splitlines()[0].strip()
    if not first_line:
        return False

    if re.match(r"^[A-Za-z_][\w.]*\([^)]*\)\s*(?:->|:)\s*\S+", first_line):
        return True
    if re.match(r"^[A-Za-z_][\w.]*\([^)]*\)$", first_line):
        return True

    return False


def get_error_log(fn) -> str:
    """Build error log from function's Context (attempts + errors)."""
    ctx = getattr(fn, 'context', None)
    if ctx is None:
        return ""

    lines: list[str] = []
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
