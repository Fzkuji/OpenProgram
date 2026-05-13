"""extract_code — pull Python code out of an LLM reply.

The reply is typically wrapped in a ```python``` markdown fence, but the
LLM sometimes drops the fence; this helper handles both cases.
"""

from __future__ import annotations

import re

from openprogram.agentic_programming.function import traced


@traced
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
