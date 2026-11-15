"""The agent that performs a memory write, supplied by the runtime.

A write runs a detached agent session on the default Agent's model. That
session belongs to the runtime layer, which sits above the store, so memory
does not build it: the runtime registers a factory here when it loads, and
:func:`writer_agent` builds one writer per write from it.

The writer's contract is :class:`MemoryWriterAgent`: ``run(prompt=,
system_prompt=, cwd=, tools=, max_turns=, output_schema=)`` returns an
``AgentResult`` or raises ``AgentExecutionError`` (both in
``agent_runtime.claude_code``).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class MemoryWriterAgent(Protocol):
    def run(self, **kwargs: Any) -> Any:
        """Run one write task and return its ``AgentResult``."""


_factory: Callable[[str | None], MemoryWriterAgent] | None = None


def register_writer_agent(factory: Callable[[str | None], MemoryWriterAgent]) -> None:
    """Make ``factory(model)`` the way this process builds a memory writer."""
    global _factory
    _factory = factory


def writer_agent(model: str | None = None) -> MemoryWriterAgent:
    """A writer on ``model`` (None: the default Agent's model)."""
    if _factory is None:
        raise RuntimeError(
            "No memory writer agent is registered; the runtime registers one "
            "when openprogram.runtime is imported."
        )
    return _factory(model)


__all__ = ["MemoryWriterAgent", "register_writer_agent", "writer_agent"]
