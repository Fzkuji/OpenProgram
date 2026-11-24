"""The agent that performs a memory write, supplied by the runtime.

A write runs a detached agent session on the default Agent's model. That
session belongs to the runtime layer, which sits above the store, so memory
does not build it: the runtime registers a factory here when it loads, and
:func:`writer_agent` builds one writer per write from it.

The writer's contract is :class:`MemoryWriterAgent`: ``run(prompt=,
system_prompt=, cwd=, tools=, max_turns=, output_schema=)`` returns an
``AgentResult`` or raises ``AgentExecutionError``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class AgentExecutionError(RuntimeError):
    """The writer could not complete an agent trajectory.

    ``turns`` carries whatever the trajectory managed before it failed,
    which is the only record of what a run that hit its turn limit spent
    those turns on.
    """

    def __init__(
        self,
        message: str,
        turns: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
        prompt: str = "",
        retryable: bool | None = None,
        reason: str | None = None,
    ):
        super().__init__(message)
        self.turns = turns or []
        self.system_prompt = system_prompt
        self.prompt = prompt
        self.retryable = retryable
        self.reason = reason


@dataclass(frozen=True)
class AgentResult:
    text: str
    structured_output: Any
    num_turns: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    # What the writer's model calls cost by the provider's price table,
    # None when the model has no price.
    anthropic_equivalent_cost_usd: float | None
    duration_ms: int
    duration_api_ms: int
    stop_reason: str | None
    session_id: str
    # Every tool call and result, in order: the record of what the turns did.
    turns: list[dict[str, Any]] = field(default_factory=list)
    # Exactly what the model was sent. Reading a trajectory back means
    # knowing what it was answering, not only what it then did.
    system_prompt: str = ""
    prompt: str = ""
    reply: str = """"""


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


__all__ = [
    "AgentExecutionError",
    "AgentResult",
    "MemoryWriterAgent",
    "register_writer_agent",
    "writer_agent",
]
