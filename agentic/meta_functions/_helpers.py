"""
Shared helpers for meta functions: code extraction, validation, compilation, saving.
"""

from __future__ import annotations

import inspect
import os
import re
from typing import Optional

from agentic.function import agentic_function
from agentic.runtime import Runtime


# ── Safety ────────���─────────────────────────────────────────────

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

# Safe standard library modules that generated code may import
_ALLOWED_IMPORTS = {
    "os", "os.path", "sys", "json", "re", "math", "datetime",
    "pathlib", "collections", "itertools", "functools",
    "textwrap", "string", "io", "csv", "hashlib", "base64",
    "time", "random", "copy", "glob", "shutil", "tempfile",
}


def _make_safe_builtins() -> dict:
    """Create a restricted builtins dict."""
    import builtins
    safe = {}
    for name in _ALLOWED_BUILTINS:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    safe["__import__"] = _safe_import
    return safe


def _safe_import(name, *args, **kwargs):
    """Allow only whitelisted standard library imports."""
    if name in _ALLOWED_IMPORTS:
        return __builtins__["__import__"](name, *args, **kwargs) if isinstance(__builtins__, dict) else __import__(name, *args, **kwargs)
    raise ImportError(
        f"Import '{name}' is not allowed in generated functions. "
        f"Allowed imports: {', '.join(sorted(_ALLOWED_IMPORTS))}"
    )


# ���─ Code extraction ───────────��────────────────────────────────

def extract_code(response: str) -> str:
    """Extract Python code from LLM response, stripping markdown fences."""
    match = re.search(r"```(?:python)?\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    lines = response.strip().splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (
            stripped.startswith("import ")
            or stripped.startswith("from ")
            or stripped.startswith("@agentic_function")
            or stripped.startswith("def ")
        ):
            return "\n".join(lines[i:]).strip()

    return response.strip()


# ─��� Validation ─────────────────────────────────────────────────

def validate_code(code: str, response: str) -> None:
    """Validate generated code: no disallowed imports, no async, valid syntax."""
    for line in (response + "\n" + code).split("\n"):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            module = stripped.split()[1].split(".")[0].rstrip(",")
            if module not in _ALLOWED_IMPORTS:
                raise ValueError(
                    f"Import '{module}' is not allowed. Allowed: {', '.join(sorted(_ALLOWED_IMPORTS))}\n{code}"
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


# ── Compilation ────────────────────────────────────────────────

def compile_function(code: str, runtime: Runtime, name: str = None) -> callable:
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

    fn = find_function(namespace)
    if fn is None:
        raise ValueError(
            f"Generated code does not contain an @agentic_function:\n{code}"
        )
    if name:
        fn.__name__ = name
        fn.__qualname__ = name

    # Bind runtime and any approved imports into the generated function's globals
    target_globals = None
    if hasattr(fn, '__wrapped__'):
        target_globals = fn.__wrapped__.__globals__
    elif hasattr(fn, '_fn') and fn._fn:
        target_globals = fn._fn.__globals__
    elif hasattr(fn, '__globals__'):
        target_globals = fn.__globals__

    if target_globals is not None:
        for key, value in namespace.items():
            if key != "__builtins__":
                target_globals[key] = value
        target_globals['runtime'] = runtime

    return fn


def find_function(namespace: dict) -> Optional[callable]:
    """Find the generated function in the namespace (agentic or regular)."""
    for obj_name, obj in namespace.items():
        if obj_name.startswith("_"):
            continue
        if isinstance(obj, agentic_function):
            return obj
    import types
    for obj_name, obj in namespace.items():
        if obj_name.startswith("_"):
            continue
        if isinstance(obj, types.FunctionType):
            return obj
    return None


def guess_name(code: str) -> Optional[str]:
    """Guess function name from generated code."""
    match = re.search(r"def\s+(\w+)\s*\(", code)
    return match.group(1) if match else None


# ── File I/O ───────────────────────────────────────────────────

def save_function(code: str, fn_name: str, description: str = None) -> str:
    """Save generated function source code to agentic/functions/."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    functions_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "functions")
    os.makedirs(functions_dir, exist_ok=True)

    init_path = os.path.join(functions_dir, "__init__.py")
    if not os.path.exists(init_path):
        with open(init_path, "w") as f:
            f.write("# Auto-generated agentic functions\n")

    header = f'"""Auto-generated by create(). Description: {description or "N/A"}"""\n\n'
    imports = "from agentic.function import agentic_function\n\n"
    file_content = header + imports + code + "\n"

    filepath = os.path.join(functions_dir, f"{fn_name}.py")
    with open(filepath, "w") as f:
        f.write(file_content)

    return filepath


def save_skill_template(fn_name: str, description: str, code: str) -> str:
    """Create a basic template SKILL.md (no LLM needed)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    skill_dir = os.path.join(repo_root, "skills", fn_name)
    os.makedirs(skill_dir, exist_ok=True)

    skill_md = f"""---
name: {fn_name}
description: "{description}"
---

# {fn_name}

{description}

## Usage

```python
from agentic.functions.{fn_name} import {fn_name}
result = {fn_name}(...)
```
"""
    filepath = os.path.join(skill_dir, "SKILL.md")
    with open(filepath, "w") as f:
        f.write(skill_md)
    return filepath


# ── Source & error helpers ─────────────────────────────────────

def get_source(fn) -> str:
    """Get source code of a function. Falls back to docstring if unavailable."""
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        doc = getattr(fn, '__doc__', '') or ''
        name = getattr(fn, '__name__', 'unknown')
        return f"# Source not available for {name}\n# Docstring: {doc}"


def get_error_log(fn) -> str:
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
