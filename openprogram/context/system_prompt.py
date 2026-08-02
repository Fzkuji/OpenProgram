"""Back-compat shim for the system-prompt assembler.

The ONE assembler lives in ``context.components`` (dag/overview.md §7) — it
holds the component registry and every layer. This module only forwards, so
existing ``from openprogram.context.system_prompt import build_system_prompt``
imports keep working. New code should import from ``context.components``.
"""
from __future__ import annotations

from typing import Any


def build_system_prompt(agent: Any, **kwargs: Any) -> str:
    """Compose the layered system prompt for ``agent``.

    Thin shim: delegates to ``context.components.build_system_prompt``.
    Accepts keyword arguments (e.g. ``channel="telegram"``) and forwards them.
    """
    from openprogram.context.components import build_system_prompt as _assemble
    return _assemble(agent, **kwargs)


__all__ = ["build_system_prompt"]
