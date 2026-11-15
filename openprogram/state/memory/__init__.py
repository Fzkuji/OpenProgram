"""Persistent, machine-wide memory for OpenProgram agents.

This package holds both the contract a memory system has to meet and the
shipped implementation of it: a Markdown workspace the model writes and
edits.

    backend.py           the contract — MemoryBackend
    local_backend.py     the shipped implementation — LocalMemoryBackend
    store.py             where memory lives on disk
    policy.py            per-execution memory access and spaces
    project_layer.py     a project's own memory, read beside the personal one
    scheduler.py         when reorganizing runs (nightly)
    session_watcher.py   when a session goes idle
    writing.py           accumulate, write, verify, reorganize
    management/          the write transaction and write verification
    retrieval/           BM25, embedding and hybrid search; layered reads
    markdown/            the topic format — blocks, footnotes, links
    prompts/             what the writer, organizer and verifier are told
    runtime/             node marks, thresholds, derived views, writer status
    agent_runtime/       the Claude Code writer and the writer result types
    writer_agent.py      the hook the runtime registers its writer through

The runtime never names an implementation. It calls ``get_backend()``,
which returns the configured one. Swapping memory systems means writing
a class that satisfies ``MemoryBackend`` and pointing ``get_backend()`` at
it; nothing in the agent loop, the tools, the web UI or the CLI changes.

Storage location: ``<state>/memory/`` (profile-global by default —
shared across every agent and conversation on the machine).
"""

from __future__ import annotations

from .backend import (
    MemoryBackend,
    MemoryWriteFailureClassification,
    MemoryWriteFailureCode,
    WriteFailure,
    classify_memory_write_failure,
)

_backend: MemoryBackend | None = None
DISABLED_MESSAGE = "memory is disabled by memory.backend=none"


class _DisabledMemoryBackend(MemoryBackend):
    @property
    def name(self) -> str:
        return "none"

    def reorganize(self, **kwargs) -> dict:
        return {"status": "disabled"}


def is_enabled() -> bool:
    from openprogram.state.config import read_config

    return ((read_config().get("memory") or {}).get("backend") != "none")


def get_backend() -> MemoryBackend:
    """The memory system in use.

    Cached: the hooks are called on every turn, and building a backend
    should not be part of that cost.
    """
    global _backend
    if _backend is None:
        if not is_enabled():
            _backend = _DisabledMemoryBackend()
        else:
            from .local_backend import LocalMemoryBackend

            _backend = LocalMemoryBackend()
    from .policy import ScopedBackend, current
    return ScopedBackend(_backend) if current() is not None else _backend


def set_backend(instance: MemoryBackend | None) -> None:
    """Install a different memory system, or reset to the default.

    Exists so a test can substitute one, and so an alternative
    implementation has a supported way in.
    """
    global _backend
    _backend = instance


__all__ = [
    "DISABLED_MESSAGE", "MemoryBackend",
    "MemoryWriteFailureClassification", "MemoryWriteFailureCode",
    "WriteFailure", "classify_memory_write_failure", "get_backend",
    "is_enabled", "set_backend",
]
