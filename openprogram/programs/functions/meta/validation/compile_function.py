"""compile_function — execute LLM-generated code in a sandbox namespace
and return the resulting callable.
"""

from __future__ import annotations

from typing import Optional

from openprogram.agentic_programming.function import agentic_function, traced
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.meta.validation.sandbox import _make_safe_builtins


@traced
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

    compiled_func = find_function(namespace)
    if compiled_func is None:
        raise ValueError(
            f"Generated code does not contain an @agentic_function:\n{code}"
        )
    if name:
        compiled_func.__name__ = name
        compiled_func.__qualname__ = name

    # Bind runtime and any approved imports into the generated function's globals.
    target_globals = None
    if hasattr(compiled_func, '__wrapped__'):
        target_globals = compiled_func.__wrapped__.__globals__
    elif hasattr(compiled_func, '_fn') and compiled_func._fn:
        target_globals = compiled_func._fn.__globals__
    elif hasattr(compiled_func, '__globals__'):
        target_globals = compiled_func.__globals__

    if target_globals is not None:
        for key, value in namespace.items():
            if key != "__builtins__":
                target_globals[key] = value
        target_globals['runtime'] = runtime

    return compiled_func


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
