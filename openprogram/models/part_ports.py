"""Where the models layer's assembly leaves what it fills into its parts' ports.

Inside the models layer a lower part reaches a higher one only through a
port it owns (``auth.provider_port``, ``auth.context``,
``providers.usage_port``, ``providers.catalog.listed_models``,
``providers.catalog.enabled_models``). The
assembly (``openprogram.models.lower_layers``) fills them when the root
package loads, before any part has. Importing a port's own module would
load its part — the provider adapters, the credential store — on every
``import openprogram``, so the assembly leaves each port's behaviours here
instead, by the port's name, and the port reads them from here. This module
imports nothing.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_filled: dict[str, dict[str, Callable[..., Any]]] = {}


def fill(port: str, **behaviours: Callable[..., Any]) -> None:
    """Leave ``behaviours`` for the port named ``port``."""
    _filled.setdefault(port, {}).update(behaviours)


def filled(port: str) -> dict[str, Callable[..., Any]]:
    """The behaviours left for ``port``: the live mapping, so what is filled
    later is seen too."""
    return _filled.setdefault(port, {})


__all__ = ["fill", "filled"]
