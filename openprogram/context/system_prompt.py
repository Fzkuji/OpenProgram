"""Layered system-prompt composer.

Order of blocks (top-to-bottom — earliest blocks are stickiest in
prefix cache):

    1. Identity banner   ("You are <name>...")
    2. Workspace files   (AGENTS.md → SOUL.md → USER.md)
    3. Inline prompt     (agent.system_prompt)
    4. Skill index       (one-line per enabled skill)
    5. Memory snapshot   (persistent BuiltinMemoryProvider block)

Wrapped in a ``── Agent prompt ──`` fence so the model sees the
boundary against the human turn that follows.
"""
from __future__ import annotations

from typing import Any


def build_system_prompt(agent: Any) -> str:
    """Compose the layered system prompt for ``agent``.

    Accepts either an ``AgentSpec`` (object with .identity / .id /
    .system_prompt / .skills) or a plain dict (webui code paths pass
    profile dicts). Falls back to ``agent.system_prompt`` text when
    layered composition fails so the LLM still sees *something*.
    """
    try:
        return _compose(agent)
    except Exception:
        # Last-ditch: just the inline prompt.
        inline = _attr(agent, "system_prompt", "") or ""
        return str(inline).strip()


def _compose(agent: Any) -> str:
    from openprogram.agents import workspace as _workspace

    parts: list[str] = []

    agent_id = _attr(agent, "id", "") or ""
    identity = _attr(agent, "identity", None)
    name = (_attr(identity, "name", "") or _attr(agent, "name", "")
            or agent_id).strip()
    header = f"You are {name} (agent_id={agent_id})."
    mentions = _attr(identity, "mention_patterns", None) or []
    if mentions:
        header += " Users may address you via: " + ", ".join(mentions) + "."
    parts.append(header)

    if agent_id:
        for reader in (_workspace.read_agents_md,
                       _workspace.read_soul_md,
                       _workspace.read_user_md):
            block = (reader(agent_id) or "").strip()
            if block:
                parts.append(block)

    inline = (_attr(agent, "system_prompt", "") or "").strip()
    if inline:
        parts.append(inline)

    skill_index = _enabled_skills_summary(agent)
    if skill_index:
        parts.append(skill_index)

    try:
        from openprogram.memory.builtin import BuiltinMemoryProvider
        mem_block = BuiltinMemoryProvider().system_prompt_block()
        if mem_block.strip():
            parts.append(mem_block)
    except Exception:
        pass

    if not parts:
        return ""
    return ("── Agent prompt ──\n"
            + "\n\n".join(parts)
            + "\n── End of agent prompt ──\n")


def _enabled_skills_summary(agent: Any) -> str:
    try:
        from openprogram.agentic_programming import (
            default_skill_dirs, load_skills,
        )
    except Exception:
        return ""
    try:
        skills = load_skills(default_skill_dirs())
    except Exception:
        return ""
    if not skills:
        return ""
    disabled_obj = _attr(agent, "skills", None) or {}
    if isinstance(disabled_obj, dict):
        disabled = set(disabled_obj.get("disabled") or [])
    else:
        disabled = set(_attr(disabled_obj, "disabled", None) or [])
    enabled = [s for s in skills if s.name not in disabled]
    if not enabled:
        return ""
    lines = ["Skills available on demand:"]
    for s in enabled[:20]:
        desc = (getattr(s, "description", "") or "").strip()
        if desc:
            desc = desc.splitlines()[0][:80]
            lines.append(f"  · {s.name} — {desc}")
        else:
            lines.append(f"  · {s.name}")
    if len(enabled) > 20:
        lines.append(f"  ... (+{len(enabled) - 20} more)")
    return "\n".join(lines)


def _attr(obj: Any, name: str, default: Any) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


__all__ = ["build_system_prompt"]
