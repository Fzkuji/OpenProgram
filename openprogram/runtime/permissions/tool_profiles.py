"""What a tool tells the permission decision about its own calls.

Permissions decide what a tool call may do, but only the tool knows what
its arguments mean: which capability a ``self_update`` action needs, what
screen a ``computer_use`` call operates and whether it runs a page script,
which files an ``apply_patch`` writes. Permissions sit in the runtime,
below the tools, and never import them. Each tool that has more to say
than its name declares a :class:`ToolProfile` and registers it here under
its name when its module loads; the permission code asks :func:`profile`.

A tool with no profile is judged by its name alone, as in a process where
no Program loaded. When the Programs have not loaded yet, the first lookup
of an unregistered name loads them (``runtime.program_host``), so a profile
is never missed because its tool had not been imported.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from openprogram.runtime import program_host

Arguments = Mapping[str, Any]


@dataclass(frozen=True)
class ToolProfile:
    """The questions about a call that only its tool can answer; ``None`` has no answer."""

    #: The capability of the authority table a call needs (``fs.read``,
    #: ``process.exec``…), when its arguments decide it.
    capability: Callable[[Arguments | None], str] | None = None
    #: Whether a call only reads, so Auto mode lets it run without review.
    reads_only: Callable[[Arguments | None], bool] | None = None
    #: Whether a call runs code inside a signed-in page.
    page_script: Callable[[Arguments | None], bool] | None = None
    #: What a call asks the user before it operates something (an object
    #: with ``shown`` and ``rule``), or ``None`` when it asks nothing. A tool
    #: that answers it operates screens: each is granted whole, once.
    approval: Callable[[Arguments | None], Any] | None = None
    #: Whether a screen call stays inside the conversation's own Pages.
    page_surface: Callable[[Arguments], bool] | None = None
    #: Whether a screen call's target resolves now.
    resolves: Callable[[Arguments], bool] | None = None
    #: The value a permission rule for a call is written under, or ``None``.
    rule_key: Callable[[Arguments | None], str | None] | None = None
    #: The card an allowed call shows the user instead of running unseen.
    interaction_manifest: Callable[[dict], dict | None] | None = None
    #: The files a call writes, whose state an approval pins.
    written_paths: Callable[[Arguments], list[str]] | None = None
    #: Whether a resolved tool is this tool's own read-only implementation
    #: (not a replacement under the same name).
    inspects: Callable[[Any], bool] | None = None


_profiles: dict[str, ToolProfile] = {}
_loading_attempted = False


def register(name: str, tool_profile: ToolProfile) -> None:
    """Declare what ``name``'s calls mean to the permission decision."""
    _profiles[name] = tool_profile


def profile(name: str) -> ToolProfile | None:
    """The profile registered for ``name``, or ``None``."""
    found = _profiles.get(name)
    if found is None and _load_programs_once():
        found = _profiles.get(name)
    return found


def _load_programs_once() -> bool:
    """Load the Programs if nothing has yet; whether this call loaded them."""
    global _loading_attempted
    if _loading_attempted or program_host.loaded():
        return False
    _loading_attempted = True
    try:
        program_host.host()
    except RuntimeError:
        return False
    return True


__all__ = ["Arguments", "ToolProfile", "profile", "register"]
