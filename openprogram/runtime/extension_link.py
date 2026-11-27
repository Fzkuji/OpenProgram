"""The link between the runtime and the extensions above it.

Skills, plugins and MCP servers (``openprogram.extensions``) sit above the
runtime: they import it, and it never imports them. What the runtime reads
from them — the skills a prompt lists — arrives through the source the
extensions register here when they load (``extensions.lower_layers``).
Commands go the other way: each extension pushes its commands into the
command registry (``runtime.commands.registry.register_source``).

Until a source is registered, the first read loads the Programs, which load
the extensions, through ``host.runtime_facts.load_programs``. With no
extensions at all a prompt lists no skills, which is what a process without
skills installed shows.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from openprogram.host import runtime_facts


class SkillSource(Protocol):
    """What the skills extension gives the runtime."""

    def list_skills(self) -> list[Any]:
        """Every visible skill, from all the sources skills come from."""

    def load_skills(self, dirs: Iterable[str]) -> list[Any]:
        """The skills found under ``dirs`` only."""

    def format_skills_for_prompt(self, skills: list[Any]) -> str:
        """The ``<available_skills>`` block listing ``skills``."""


_skills: SkillSource | Any | None = None


def register_skills(source: SkillSource | Any) -> None:
    """Install the skills source (the last one wins)."""
    global _skills
    _skills = source


def skills() -> SkillSource | Any | None:
    """The skills source, loading the extensions first if needed; None
    when there is none."""
    if _skills is None:
        runtime_facts.load_programs()
    return _skills


__all__ = ["SkillSource", "register_skills", "skills"]
