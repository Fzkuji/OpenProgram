"""Agent: tool loop = repeatedly call llm + execute tools until done."""
from __future__ import annotations

from typing import Any

from openprogram.providers.structured_output import JsonSchemaOutput


def agent(
    prompt: str | list[dict],
    *,
    model: str = "",
    effort: str = "",
    tools: list[Any] | None = None,
    tools_deny: list[str] | None = None,
    response_format: dict[str, Any] | JsonSchemaOutput | None = None,
    max_iterations: int = 20,
    timeout_s: float | None = None,
    tool_choice: Any = None,
    parallel_tool_calls: bool | None = None,
    execution_kind: str = "agent",
    runtime=None,
    return_raw: bool = False,
) -> Any:
    """Run a tool loop: model calls tools, sees results, continues until done.

    Args:
        prompt: Initial prompt (string or content blocks)
        model: Model override (empty = use session default)
        effort: Reasoning effort override
        tools: Tool names to provide (None = all available tools)
        response_format: JSON Schema or JsonSchemaOutput contract (None = return text)
        tools_deny: Tool names that must remain unavailable for this turn
        execution_kind: Runtime execution label for this tool loop
        max_iterations: Max tool loop rounds
        timeout_s: Timeout in seconds

    Returns:
        Final text, or the validated JSON value when response_format is set
    """
    from openprogram.agentic_programming.function import _current_runtime

    if runtime is None:
        runtime = _current_runtime.get(None)
    if runtime is None:
        raise RuntimeError(
            "agent() requires an ambient Runtime; call it inside an "
            "@agentic_function or another runtime-bound execution context."
        )

    if isinstance(prompt, str):
        content = [{"type": "text", "text": prompt}]
    elif isinstance(prompt, list):
        content = prompt
    else:
        raise TypeError("agent() prompt must be a string or a list of content blocks")

    exec_options: dict[str, Any] = {
        "content": content,
        "model": model or None,
        "tools": tools,  # None = default tools; [] = no tools; [...] = named tools
        "max_iterations": max_iterations,
        "timeout_s": timeout_s,
        "effort": effort or None,
        "execution_kind": execution_kind,
    }
    if response_format is not None:
        exec_options["response_format"] = response_format
    if tools_deny is not None:
        exec_options["tools_deny"] = tools_deny
    if tool_choice is not None:
        exec_options["tool_choice"] = tool_choice
    if parallel_tool_calls is not None:
        exec_options["parallel_tool_calls"] = parallel_tool_calls
    result = runtime.exec(**exec_options)

    if response_format is not None:
        return result
    if return_raw:
        return result

    # Preserve the legacy text result contract for unstructured calls.
    if isinstance(result, dict) and "text" in result:
        return result["text"]
    return str(result)


__all__ = ["agent"]
