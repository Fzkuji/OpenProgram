"""The ``skill`` tool implementation."""
from __future__ import annotations

import os

from openprogram.functions._runtime import function

_DESC = (
    "Load a skill's instructions into this turn. Skills are the named "
    "entries in the <available_skills> block of the system prompt; each is "
    "a SKILL.md holding a worked procedure for one kind of task. Call this "
    "with the skill's name when the task matches its description. The "
    "listing carries only a one-line summary; the full instructions come "
    "from here. Accepts the full hierarchical name "
    "(`anthropic-skills/docx`) or any unambiguous short form (`docx`). "
    "Relative paths inside the returned body resolve against the skill's "
    "own directory, which is reported alongside the body."
)

_MAX_LISTED_FILES = 50


@function(
    name="skill",
    description=_DESC,
    toolset=["core"],
    max_result_chars=60_000,
)
def skill(name: str = "") -> str:
    """Return a skill's SKILL.md body.

    Args:
        name: Skill to load, as shown in `<available_skills>`. The full
            hierarchical path or any unambiguous short form (alias, last
            path segment, path suffix, or substring).
    """
    from openprogram.skills.loader import (
        AmbiguousSkillError, get_skill, resolve, skill_resource_tree,
    )
    from openprogram.skills.tool import invoke

    query = (name or "").strip()
    if not query:
        return "[skill error] pass a skill name, see <available_skills>."

    try:
        found = get_skill(query) or resolve(query)
    except AmbiguousSkillError as e:
        return (
            f"[skill error] {query!r} matches several skills: "
            f"{', '.join(e.candidates)}. Pass the full name."
        )
    if found is None:
        return (
            f"[skill error] no skill named {query!r}. The available names "
            f"are in the <available_skills> block."
        )

    try:
        body = invoke(found.name)
    except Exception as e:  # noqa: BLE001 — tool results report, never raise
        return f"[skill error] {type(e).__name__}: {e}"

    base_dir = os.path.dirname(found.path)
    files = skill_resource_tree(found)
    head = f'<skill name="{found.name}" directory="{base_dir}">'
    tail = "</skill>"
    if files:
        listed = "\n".join(f"  {f}" for f in files[:_MAX_LISTED_FILES])
        extra = (
            f"\n  ... (+{len(files) - _MAX_LISTED_FILES} more)"
            if len(files) > _MAX_LISTED_FILES else ""
        )
        tail = f"<skill_files>\n{listed}{extra}\n</skill_files>\n{tail}"
    return f"{head}\n{body}\n{tail}"
