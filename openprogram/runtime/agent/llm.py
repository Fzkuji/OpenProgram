"""Make one model request through the current or an owned execution scope."""
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
    """Return a model response, with the declared structured-output repair budget."""
    from openprogram.runtime.agent.call_state import _current_runtime

    runtime = _current_runtime.get(None)
    if runtime is None:
        from openprogram.runtime.agent.runtime_scope import runtime_scope

        with runtime_scope():
            return llm(prompt, model=model, effort=effort, response_format=response_format, choices=choices, web_search=web_search, timeout_s=timeout_s)

    if isinstance(prompt, str):
        content = [{"type": "text", "text": prompt}]
    elif isinstance(prompt, list):
        content = prompt
    else:
        raise TypeError("llm() prompt must be a string or a list of content blocks")
    iterations = 1
    if response_format is not None:
        from openprogram.models.providers.structured_output import normalize_response_format

        response_format = normalize_response_format(response_format)
        iterations += response_format.max_validation_retries
    return runtime.exec(
        content=content, response_format=response_format, model=model or None,
        tools=[], max_iterations=iterations, choices=choices, timeout_s=timeout_s,
        web_search=web_search, effort=effort or None, execution_kind="llm",
    )


__all__ = ["llm"]
