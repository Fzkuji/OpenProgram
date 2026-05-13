"""Sandbox primitives used when executing LLM-generated code in-process.

These allowlists keep generated functions from importing arbitrary modules
or touching dangerous builtins. Consumers go through ``_make_safe_builtins``.
"""

from __future__ import annotations


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

# Safe standard library modules that generated code may import.
_ALLOWED_IMPORTS = {
    "os", "os.path", "sys", "json", "re", "math", "datetime",
    "pathlib", "collections", "itertools", "functools",
    "textwrap", "string", "io", "csv", "hashlib", "base64",
    "time", "random", "copy", "glob", "shutil", "tempfile",
    "urllib", "urllib.parse", "urllib.request",
    "typing", "dataclasses", "enum", "abc",
    "statistics", "decimal", "fractions",
}


def _safe_import(name, *args, **kwargs):
    """Allow only whitelisted standard library imports."""
    if name in _ALLOWED_IMPORTS:
        return (
            __builtins__["__import__"](name, *args, **kwargs)
            if isinstance(__builtins__, dict)
            else __import__(name, *args, **kwargs)
        )
    raise ImportError(
        f"Import '{name}' is not allowed in generated functions. "
        f"Allowed imports: {', '.join(sorted(_ALLOWED_IMPORTS))}"
    )


def _make_safe_builtins() -> dict:
    """Create a restricted builtins dict for generated-code execution."""
    import builtins
    safe = {}
    for name in _ALLOWED_BUILTINS:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    safe["__import__"] = _safe_import
    return safe
