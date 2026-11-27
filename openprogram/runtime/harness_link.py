"""The outside agents a conversation's turn can run in, as the conversation
runtime reaches them.

A conversation whose model belongs to a harness provider (Codex, Claude
Code, DeepSeek Harness) hands each turn to that agent's engine
(``openprogram.runtime.harnesses``). The engines sit above the conversation
runtime: they run a turn with its event parsing, working directory and
steering. So the conversation runtime never imports them; this module is the
link between them and imports no part. The runtime's
assembly (``openprogram.runtime.lower_layers``) registers the two questions
it asks when the runtime loads, and each provider imports the harnesses when
it is called.

Without a registered provider no turn runs in an outside agent.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

LINKS = frozenset({"turn_engine", "runs_in_harness"})

_providers: dict[str, Callable[..., Any]] = {}


def register(**providers: Callable[..., Any]) -> None:
    """Install the harnesses' provider for each named link."""
    unknown = set(providers) - LINKS
    if unknown:
        raise ValueError(f"unknown harness links: {sorted(unknown)}")
    _providers.update(providers)


def turn_engine(req: Any) -> Any:
    """The outside agent's engine a turn runs in (its ``run_turn``), or None
    for OpenProgram's own agent loop."""
    provider = _providers.get("turn_engine")
    return provider(req) if provider is not None else None


def runs_in_harness(request: Any) -> bool:
    """Whether the turn this request (a stored turn request) starts runs in an
    outside agent."""
    provider = _providers.get("runs_in_harness")
    return bool(provider(request)) if provider is not None else False


__all__ = ["LINKS", "register", "runs_in_harness", "turn_engine"]
