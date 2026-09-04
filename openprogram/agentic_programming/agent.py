"""Agent: tool loop = repeatedly call llm + execute tools until done."""
from __future__ import annotations

from typing import Any


def agent(
    prompt: str | list[dict],
    *,
    model: str = "",
    effort: str = "",
    tools: list[Any] | None = None,
    tools_deny: list[str] | None = None,
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
        tools_deny: Tool names that must remain unavailable for this turn
        max_iterations: Max tool loop rounds
        timeout_s: Timeout in seconds
        tool_choice: Optional provider tool-selection constraint
        parallel_tool_calls: Whether the provider may call tools in parallel
        execution_kind: Runtime execution label for this tool loop
        runtime: Explicit Runtime for reusable internal loops; otherwise ambient
        return_raw: Return the Runtime result instead of extracting final text

    Returns:
        Final text result
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

    exec_kwargs = dict(
        content=content,
        model=model or None,
        tools=tools,  # None = use default tools; [] = no tools; [...] = specific tools
        max_iterations=max_iterations,
        timeout_s=timeout_s,
        effort=effort or None,
        execution_kind=execution_kind,
    )
    if tool_choice is not None:
        exec_kwargs["tool_choice"] = tool_choice
    if tools_deny is not None:
        exec_kwargs["tools_deny"] = tools_deny
    if parallel_tool_calls is not None:
        exec_kwargs["parallel_tool_calls"] = parallel_tool_calls
    result = runtime.exec(**exec_kwargs)

    # runtime.exec returns dict with 'text' key for agent execution
    if return_raw:
        return result
    if isinstance(result, dict) and "text" in result:
        return result["text"]
    return str(result)


__all__ = ["agent"]
