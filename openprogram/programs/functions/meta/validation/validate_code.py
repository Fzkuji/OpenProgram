"""validate_code — static checks before compiling LLM-generated code.

Rejects disallowed imports, async definitions, and syntax errors.
"""

from __future__ import annotations

from openprogram.agentic_programming.function import traced
from openprogram.programs.functions.meta.validation.sandbox import _ALLOWED_IMPORTS


@traced
def validate_code(code: str, response: str) -> None:
    """Validate generated code: no disallowed imports, no async, valid syntax."""
    for line in (response + "\n" + code).split("\n"):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            module = stripped.split()[1].split(".")[0].rstrip(",")
            # Allow framework imports (agentic_function, Runtime, etc.)
            if module == "openprogram":
                continue
            if module not in _ALLOWED_IMPORTS:
                raise ValueError(
                    f"Import '{module}' is not allowed. "
                    f"Allowed: {', '.join(sorted(_ALLOWED_IMPORTS))}\n{code}"
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
