"""Persistent, machine-wide memory for OpenProgram agents.

This package is the framework side: the contract a memory system has to
meet, and the points at which the runtime calls it. One implementation
ships with it, in ``scriptorium/``.

    provider.py          the contract — MemoryProvider
    store.py             where memory lives on disk
    scheduler.py         when reorganizing runs (nightly)
    session_watcher.py   when a session goes idle
    scriptorium/         the shipped implementation

The runtime never names an implementation. It calls ``get_provider()``,
which returns the configured one. Swapping memory systems means writing
a class that satisfies ``MemoryProvider`` and pointing ``get_provider()`` at
it; nothing in the agent loop, the tools, the web UI or the CLI changes.

Storage location: ``<state>/memory/`` (profile-global by default —
shared across every agent and conversation on the machine).
"""

from __future__ import annotations

from .provider import MemoryProvider

_provider: MemoryProvider | None = None
DISABLED_MESSAGE = "memory is disabled by memory.backend=none"


class _DisabledMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "none"

    def reorganize(self, **kwargs) -> dict:
        return {"status": "disabled"}


def is_enabled() -> bool:
    from openprogram.setup import _read_config

    return ((_read_config().get("memory") or {}).get("backend") != "none")


def get_provider() -> MemoryProvider:
    """The memory system in use.

    Cached: the hooks are called on every turn, and building a provider
    should not be part of that cost.
    """
    global _provider
    if _provider is None:
        if not is_enabled():
            _provider = _DisabledMemoryProvider()
        else:
            from .scriptorium import ScriptoriumMemoryProvider

            _provider = ScriptoriumMemoryProvider()
    return _provider


def set_provider(instance: MemoryProvider | None) -> None:
    """Install a different memory system, or reset to the default.

    Exists so a test can substitute one, and so an alternative
    implementation has a supported way in.
    """
    global _provider
    _provider = instance


__all__ = [
    "DISABLED_MESSAGE", "MemoryProvider", "get_provider", "is_enabled",
    "set_provider",
]
