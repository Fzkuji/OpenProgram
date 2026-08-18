"""Persistent, machine-wide memory for OpenProgram agents.

This package holds both the contract a memory system has to meet and the
shipped implementation of it: a Markdown workspace the model writes and
edits.

    backend.py           the contract — MemoryBackend
    local_backend.py     the shipped implementation — LocalMemoryBackend
    store.py             where memory lives on disk
    scheduler.py         when reorganizing runs (nightly)
    session_watcher.py   when a session goes idle
    writing.py           accumulate, write, reorganize
    management/          the write transaction: staging, validation, install
    retrieval/           BM25 and embedding search over the workspace
    markdown/            the topic format — blocks, footnotes, links
    prompts/             what the writer is told
    runtime/             cursors, thresholds, derived views
    agent_runtime/       the process that performs a write

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
    from openprogram.setup import _read_config

    return ((_read_config().get("memory") or {}).get("backend") != "none")


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
    return _backend


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
