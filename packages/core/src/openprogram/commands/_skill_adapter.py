"""Project every loaded skill into the slash-command registry.

A skill's SKILL.md body becomes the command body; the typical
invocation pattern is ``context: fork`` (run as a subagent) because
skills tend to be longer-running tasks. Until Phase 6 wires fork
mode into the dispatcher we register them as ``context: inline`` —
the rendered SKILL body lands in the textarea and the user reviews
before sending, same as user-authored commands.

Hierarchical skill names (``research/literature/survey``) are kept
verbatim; a user types ``/research/literature/survey``. Leaf-only
aliases would collide too easily across categories.
"""
from __future__ import annotations

from .frontmatter import ParsedCommand
from . import registry as _reg


def sync_into_registry() -> None:
    """Re-snapshot every visible skill into the ``skill`` bucket.
    Safe to call repeatedly — the registry handles override."""
    try:
        from openprogram.skills.loader import list_skills
    except Exception:
        return

    try:
        skills = list_skills()
    except Exception:
        return

    for sk in skills:
        name = (sk.name or "").strip()
        if not name:
            continue
        raw = ParsedCommand(
            name=name,
            description=sk.description or "",
            when_to_use="",
            body=sk.body or sk.description or "",
            allowed_tools=list(sk.allowed_tools or []),
            version=sk.version or "",
            # Skills are typically multi-step tasks; default the
            # subagent_type so a future ``context: fork`` flip lands
            # on the right pool.
            agent="general-purpose",
        )
        _reg.register_external(
            name, source="skill",
            source_label=f"(skill:{sk.source})" if sk.source else "(skill)",
            raw=raw,
            path=sk.path or "",
        )
