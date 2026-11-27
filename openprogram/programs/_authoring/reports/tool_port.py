"""The tools a report works through, as the report helpers ask for them.

The report helpers are authoring code, which sits below the tools (the
tools use ``_authoring``'s helpers). A report still reads conversations,
lists agents and saves its draft through the very tools the model calls,
so their permission checks apply, gates those tools with the turn's tool
policy, and on macOS asks which applications run. ``programs._host.lower_layers`` registers one provider per tool here
when the Programs load; each provider imports its tool when it is called.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_providers: dict[str, Callable[..., Any]] = {}

TOOLS = frozenset({
    "apply_tool_policy",
    "read_conversation",
    "conversation_session",
    "list_agents",
    "write_file",
    "running_applications",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install the provider for each named tool."""
    unknown = set(providers) - TOOLS
    if unknown:
        raise ValueError(f"unknown report tools: {sorted(unknown)}")
    _providers.update(providers)


def _provider(name: str) -> Callable[..., Any]:
    provider = _providers.get(name)
    if provider is None:
        raise RuntimeError(f"the Programs did not load: no {name} for reports")
    return provider


def apply_tool_policy(tools: list[Any], **policy: Any) -> list[Any]:
    """``tools`` through the Programs' policy chain (``programs.apply_tool_policy``)."""
    return _provider("apply_tool_policy")(tools, **policy)


def read_conversation() -> Any:
    """The ``read_conversation`` tool."""
    return _provider("read_conversation")()


def conversation_session() -> str | None:
    """The conversation ``read_conversation`` reads when its call names none."""
    return _provider("conversation_session")()


def list_agents() -> Any:
    """The ``list_agents`` tool."""
    return _provider("list_agents")()


def write_file(path: str, content: str) -> Any:
    """Write through the checked ``write`` tool; its result text."""
    return _provider("write_file")(path, content)


def running_applications(appkit: Any) -> list[Any]:
    """The applications running on this Mac, as ``NSRunningApplication``."""
    return _provider("running_applications")(appkit)


__all__ = [
    "apply_tool_policy",
    "conversation_session",
    "list_agents",
    "read_conversation",
    "register",
    "running_applications",
    "write_file",
]
