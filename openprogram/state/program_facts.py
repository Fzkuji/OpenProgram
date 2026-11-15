"""What Programs tell the state layer about themselves.

State sits below Programs and never imports them. A few state readers
still need a fact only a Program has: the registered web-search backends
(the ``tools.web_search.backend`` choice list), the registered Agent tools
(one settings toggle per tool) and the Goal tool's marks on transcript
messages (the session graph). Programs register one provider per fact here
when they load (``openprogram.programs._host.lower_layers``); the functions below
ask for it.

Without a registered provider the answer is what it is in a process where
no Program ever loaded: no web-search backends, no Agent tools and the
messages as they are.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_providers: dict[str, Callable[..., Any]] = {}

FACTS = frozenset({
    "web_search_provider_names",
    "agent_tool_names",
    "annotate_messages",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install the Programs' provider for each named fact."""
    unknown = set(providers) - FACTS
    if unknown:
        raise ValueError(f"unknown program facts: {sorted(unknown)}")
    _providers.update(providers)


def web_search_provider_names() -> list[str]:
    """Names of the registered web-search backends, in registry order."""
    provider = _providers.get("web_search_provider_names")
    return list(provider()) if provider else []


def agent_tool_names() -> list[str]:
    """Names of the Agent tools the framework can expose to a model."""
    provider = _providers.get("agent_tool_names")
    return list(provider()) if provider else []


def annotate_messages(session_id: str, messages: list[dict]) -> list[dict]:
    """``messages`` with the Goal tool's verification marks applied."""
    provider = _providers.get("annotate_messages")
    return provider(session_id, messages) if provider else messages


__all__ = [
    "FACTS",
    "agent_tool_names",
    "annotate_messages",
    "register",
    "web_search_provider_names",
]
