"""What the layers above tell the platform layer.

``host._platform`` is the bottom layer: filesystem, locking, processes and
the terminal. It never imports the state or host layers above it, but two
of its answers depend on them: which bash the owner named in the settings
(``shell.windows_bash_path``, ``execution.prevent_idle_sleep``) and whether
the sandbox is in force and can run here (which shell a command runs in,
and the platform advice ``openprogram doctor`` prints). The host layer
registers one provider per fact here when it loads
(``openprogram.host.lower_layers``, installed by the root package); the
functions below ask for it.

Without a registered provider each answer is what it is in a process where
neither layer loaded: no setting is stored, and there is no sandbox.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_providers: dict[str, Callable[..., Any]] = {}

FACTS = frozenset({
    "setting_value",
    "sandbox_in_force",
    "sandbox_unavailable_reason",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install the provider for each named fact."""
    unknown = set(providers) - FACTS
    if unknown:
        raise ValueError(f"unknown platform facts: {sorted(unknown)}")
    _providers.update(providers)


def setting_value(key: str) -> Any:
    """A setting's stored value, or its default (``state.settings``)."""
    provider = _providers.get("setting_value")
    return provider(key) if provider else None


def sandbox_in_force(policy: Any = None) -> bool:
    """Whether a command runs sandboxed: there is a policy (``policy``, or
    the one the configuration sets) and the sandbox can run here."""
    provider = _providers.get("sandbox_in_force")
    return bool(provider(policy)) if provider else False


def sandbox_unavailable_reason() -> str | None:
    """Why the sandbox cannot run on this machine, or None when it can."""
    provider = _providers.get("sandbox_unavailable_reason")
    return provider() if provider else "the sandbox is not loaded in this process"


__all__ = [
    "FACTS",
    "register",
    "sandbox_in_force",
    "sandbox_unavailable_reason",
    "setting_value",
]
