"""Registry-based context assembly (registry of ContextComponents).

Design: docs/design/context/context-composition.md. Instead of hardcoding the
system-prompt blocks in one function, each piece of context is a registered
``ContextComponent`` declaring its layer (L0/L1/L2), in-layer order, an
appearance condition, and a builder. The assembler collects the registered
components for a layer, sorts by order, drops the ones whose condition is
false, builds the rest, and joins them.

This file is step 1 of the refactor: it reproduces the existing 5 system-prompt
blocks (identity / workspace files / inline prompt / skills / memory) as L0/L1
components and assembles them **byte-for-byte identical** to the old
``system_prompt._compose``. Call tree (L1), situation (L2), and the missing
components are added in later steps.

Layers (see design doc §一):
    L0  system-level  — always present, constant for the whole session
    L1  session/project-level — carried forward; project files + call tree
    L2  task-level — this-call only (situation + current input/output)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

Layer = Literal["L0", "L1", "L2"]


def _attr(obj: Any, name: str, default: Any) -> Any:
    """Read ``name`` off an AgentSpec object or a plain dict (webui passes
    profile dicts). Mirrors system_prompt._attr."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass(frozen=True)
class ContextComponent:
    """One registered piece of context.

    name       identifier (for debugging / dedup).
    layer      which layer it belongs to (L0/L1/L2).
    order      in-layer sort key — smaller = earlier = more stable (cache).
    condition  returns True when this component should appear this turn.
               Defaults to always-on.
    build      produces the component's text; "" / None ⇒ contributes nothing.
    cacheable  whether it sits in the cacheable prefix (used by later cache
               steps; not consumed yet).
    """
    name: str
    layer: Layer
    order: int
    build: Callable[[Any], Optional[str]]
    condition: Callable[[Any], bool] = field(default=lambda ctx: True)
    cacheable: bool = True


# Three registries — one per layer. Populated at import time below.
_REGISTRY: dict[Layer, list[ContextComponent]] = {"L0": [], "L1": [], "L2": []}


def register(component: ContextComponent) -> None:
    """Register a component into its layer. Idempotent on name within a layer
    (re-registering the same name replaces the old one — handy for tests)."""
    bucket = _REGISTRY[component.layer]
    for i, existing in enumerate(bucket):
        if existing.name == component.name:
            bucket[i] = component
            return
    bucket.append(component)


def assemble(agent: Any, layers: list[Layer]) -> list[str]:
    """Collect → sort by order → filter by condition → build → drop empties.

    Returns the list of non-empty block strings, in order, across the given
    layers (layers are concatenated in the order requested; within each layer
    components are ordered by ``order``)."""
    parts: list[str] = []
    for layer in layers:
        for comp in sorted(_REGISTRY[layer], key=lambda c: c.order):
            try:
                if not comp.condition(agent):
                    continue
                text = comp.build(agent)
            except Exception:
                continue
            if text and str(text).strip():
                parts.append(str(text))
    return parts


# ── System prompt: same fence as the legacy _compose ──────────────────────

_FENCE_OPEN = "── Agent prompt ──\n"
_FENCE_CLOSE = "\n── End of agent prompt ──\n"


def build_system_prompt(agent: Any) -> str:
    """Compose the system prompt from registered L0 + L1-project components.

    Byte-for-byte equivalent to the legacy system_prompt._compose: identity
    header always present, then workspace files / inline / skills / memory when
    non-empty, joined by blank lines, wrapped in the Agent-prompt fence. Falls
    back to the inline prompt on any failure (same as legacy)."""
    try:
        # L1 here covers only the project-level system blocks that legacy put in
        # the prompt (workspace files). Call tree (also L1) is a message-side
        # component handled separately — not part of the system-prompt fence.
        parts = assemble(agent, ["L0", "L1"])
        if not parts:
            return ""
        return _FENCE_OPEN + "\n\n".join(parts) + _FENCE_CLOSE
    except Exception:
        inline = _attr(agent, "system_prompt", "") or ""
        return str(inline).strip()


# ── Builders for the existing 5 blocks (migrated from system_prompt) ───────

def _build_identity(agent: Any) -> str:
    agent_id = _attr(agent, "id", "") or ""
    identity = _attr(agent, "identity", None)
    name = (_attr(identity, "name", "") or _attr(agent, "name", "")
            or agent_id).strip()
    header = f"You are {name} (agent_id={agent_id})."
    mentions = _attr(identity, "mention_patterns", None) or []
    if mentions:
        header += " Users may address you via: " + ", ".join(mentions) + "."
    return header


def _build_workspace_files(agent: Any) -> str:
    agent_id = _attr(agent, "id", "") or ""
    if not agent_id:
        return ""
    from openprogram.agent.management import workspace as _workspace
    blocks: list[str] = []
    for reader in (_workspace.read_agents_md,
                   _workspace.read_soul_md,
                   _workspace.read_user_md):
        block = (reader(agent_id) or "").strip()
        if block:
            blocks.append(block)
    # Legacy appended each workspace file as its OWN part (separate \n\n join),
    # so reproduce that: join with the same blank-line separator the assembler
    # uses between parts.
    return "\n\n".join(blocks)


def _build_inline(agent: Any) -> str:
    return (_attr(agent, "system_prompt", "") or "").strip()


def _build_skills(agent: Any) -> str:
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


def _build_memory(agent: Any) -> str:
    try:
        from openprogram.memory.builtin import BuiltinMemoryProvider
        mem_block = BuiltinMemoryProvider().system_prompt_block()
        if mem_block.strip():
            return mem_block
    except Exception:
        pass
    return ""


# ── Register the 5 legacy blocks ──────────────────────────────────────────
# Orders preserve the legacy top-to-bottom order. identity(L0) → workspace(L1)
# → inline(L0 inline)… legacy interleaved them in one list; to stay byte-equal
# we keep the exact sequence identity, workspace, inline, skills, memory.
# We model that ordering across L0/L1 by giving global-ascending order numbers
# and assembling ["L0","L1"] won't reproduce the interleave — so for step 1 we
# register ALL five in L0 with ascending order to guarantee identical sequence.

register(ContextComponent("identity", "L0", 10, _build_identity))
register(ContextComponent("workspace_files", "L0", 20, _build_workspace_files))
register(ContextComponent("inline_prompt", "L0", 30, _build_inline))
register(ContextComponent("skills_index", "L0", 40, _build_skills))
register(ContextComponent("memory_global", "L0", 50, _build_memory))


__all__ = [
    "ContextComponent",
    "register",
    "assemble",
    "build_system_prompt",
]
