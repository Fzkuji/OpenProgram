"""One model request using the ambient agentic-programming Runtime."""
from __future__ import annotations

from typing import Any


def llm(
    prompt: str | list[dict],
    *,
    model: str = "",
    effort: str = "",
    response_format: Any = None,
    choices: Any = None,
    web_search: bool = False,
    timeout_s: float | None = None,
) -> str | dict:
    """Return one model response without creating an agent branch or tool loop."""
    from openprogram.agentic_programming.function import _current_runtime

    runtime = _current_runtime.get(None)
    if runtime is None:
        raise RuntimeError(
            "llm() requires an ambient Runtime; call it inside an "
            "@agentic_function or another runtime-bound execution context."
        )
    if isinstance(prompt, str):
        content = [{"type": "text", "text": prompt}]
    elif isinstance(prompt, list):
        content = prompt
    else:
        raise TypeError("llm() prompt must be a string or a list of content blocks")
    return runtime.exec(
        content=content,
        response_format=response_format,
        model=model or None,
        tools=[],
        max_iterations=1,
        choices=choices,
        timeout_s=timeout_s,
        web_search=web_search,
        effort=effort or None,
        execution_kind="llm",
    )


__all__ = ["llm"]
