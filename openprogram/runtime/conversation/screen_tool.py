"""What a turn needs from the screen tool.

A conversation turn treats one tool specially: the screen tool
(``computer_use``), which operates screens, application windows and browser
Pages. The turn keeps it in the tool list while a target is set up, tells
the model what it can operate (the devices section beside the message), and
asks whether a call that only looks at the conversation's own Pages would be
allowed before it captures them. The tool itself is a Program
(``openprogram.programs.tools.computer_use``) and the runtime never imports
it: this port owns the tool's name and the questions the turn asks, and the
tool fills in the answers when it loads.

Without the tool loaded, each answer is the one of a process where it never
loaded: no target is set up, nothing can be operated, no devices note, no
call that looks at Pages. Each question loads the Programs first, as the
turn's other Program lookups do (``turn_hooks``), and each provider resolves
the tool's function when it is called, so a test that replaces one is what
the turn sees.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from openprogram.host import runtime_facts

# The name the screen tool registers under.
NAME = "computer_use"

BEHAVIOURS = frozenset({
    "available_targets",
    "devices",
    "devices_note",
    "page_surface_call",
})

_providers: dict[str, Callable[..., Any]] = {}


def register(**providers: Callable[..., Any]) -> None:
    """Install the screen tool's provider for each named behaviour."""
    unknown = set(providers) - BEHAVIOURS
    if unknown:
        raise ValueError(f"unknown screen tool behaviours: {sorted(unknown)}")
    _providers.update(providers)


def _provider(name: str) -> Callable[..., Any] | None:
    runtime_facts.load_programs()
    return _providers.get(name)


def available_targets() -> list[str]:
    """The targets set up now, in the order the devices section lists them."""
    provider = _provider("available_targets")
    return list(provider()) if provider else []


def devices(session_id: str) -> dict[str, str]:
    """What can be operated now, by device: ``{device: its lines}``."""
    provider = _provider("devices")
    return provider(session_id) if provider else {}


def devices_note(previous: Optional[dict[str, str]], current: dict[str, str]) -> str:
    """What a turn tells the model about the devices, or "" when nothing changed."""
    provider = _provider("devices_note")
    return provider(previous, current) if provider else ""


def page_surface_call() -> dict | None:
    """A call that only looks at the conversation's own Pages, or None without one."""
    provider = _provider("page_surface_call")
    return provider() if provider else None


__all__ = [
    "BEHAVIOURS",
    "NAME",
    "available_targets",
    "devices",
    "devices_note",
    "page_surface_call",
    "register",
]
