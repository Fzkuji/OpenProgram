"""_retry_choice — internal helper used by parse_args to ask the LLM to
re-pick an option after the previous reply failed to parse / validate.

The function itself is wrapped in ``@agentic_function`` so that the
retry call appears in the Context tree and is traced like any other
LLM-driven step. All decorator settings are left at defaults.
"""

from __future__ import annotations

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


@agentic_function
def _retry_choice(prev_reply: str, error_msg: str, menu: str, runtime: Runtime) -> str:
    """Re-pick an option from the menu below.

    Your previous reply had a problem. Look at the error description
    and the available options, then reply with valid JSON in the format
    shown in the Call: example line. JSON only, no prose."""
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Previous reply:\n{prev_reply[:500]}\n\n"
            f"Problem: {error_msg}\n\n"
            f"Options:\n{menu}"
        )},
    ])
