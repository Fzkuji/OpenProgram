"""The ``chat`` action's ``/skill <name>`` expansion phase."""
from __future__ import annotations

from .session_agent import _db_agent_id
from .turn_request import _ChatRequest


def _skill_gate_error(resolved, req: _ChatRequest) -> str | None:
    # Agent-profile gating — shared helper across all
    # extension types (tools / skills / mcp). Patterns
    # support fnmatch wildcards.
    gate_error: str | None = None
    if resolved is not None:
        try:
            from openprogram.runtime.agent_registry import manager as _A
            from openprogram.extensions.gating import gate as _gate
            from openprogram.runtime.conversation.session_config import load_agent_session_binding
            session_id = req.session_id
            agent_invocation = req.agent_invocation
            bound = load_agent_session_binding(session_id) if session_id else {}
            profile = (agent_invocation["profile_snapshot"]
                       if agent_invocation is not None else bound.get("profile_snapshot"))
            ag = _A.get(req.agent_id or _db_agent_id(session_id)) if profile is None else None
            prof = profile.get("skills", {}) if profile is not None else ag.to_dict().get("skills", {}) if ag else {}
            gate_error = _gate(
                name=resolved.name,
                category=resolved.category or "",
                disabled=prof.get("disabled") or [],
                allowed=prof.get("allowed") or [],
                categories=prof.get("categories") or [],
            )
        except Exception as e:
            gate_error = (
                f"Could not evaluate skill gating for "
                f"{(resolved.name if resolved else 'unknown')!r}: "
                f"{type(e).__name__}: {e}"
            )
    return gate_error


def _is_skill_command(text: str) -> bool:
    return text.lower().startswith("/skill ") and bool(text[len("/skill "):].strip())


def _split_skill_command(text: str) -> tuple[str, str]:
    """``/skill <name> [request]`` as ``(name, request)``."""
    head, _, tail = text[len("/skill "):].strip().partition(" ")
    return head.strip(), tail.strip()


def _expand_skill_command(req: _ChatRequest) -> tuple[str, str] | None:
    """Load the skill a ``/skill <name> [request]`` message names.

    The typed message stays the user's visible message. The skill's
    SKILL.md body is kept on ``req.skill_activation`` and reaches the
    model only, through :func:`_skill_model_text`. Returns
    ``(code, message)`` when the skill cannot be used; the turn then
    ends without a model call.
    """
    if not _is_skill_command(req.text):
        return None
    skill_name, _ = _split_skill_command(req.text)
    try:
        from openprogram.extensions.skills.tool import invoke as _skill_invoke
        from openprogram.extensions.skills.loader import (
            AmbiguousSkillError, get_skill, resolve as _skill_resolve,
        )
        # Resolve the skill first so we have a stable object to
        # gate on. The actual invoke (which writes a trace
        # entry) only happens after the gate passes.
        try:
            resolved = get_skill(skill_name) or _skill_resolve(skill_name)
        except AmbiguousSkillError as e:
            return "skill_ambiguous", (
                f"Skill name {skill_name!r} is ambiguous. "
                f"Candidates: {', '.join(e.candidates)}. "
                f"Retry with the full hierarchical name."
            )
        if resolved is None:
            return "skill_not_installed", (
                f"Skill {skill_name!r} not installed. "
                f"Browse /skills to install it first."
            )
        gate_error = _skill_gate_error(resolved, req)
        if gate_error:
            # Profile-level gate rejection: the user adjusts the agent
            # profile or picks a different skill.
            return "skill_blocked", f"Skill blocked: {gate_error}"
        try:
            skill_md = _skill_invoke(skill_name)
        except KeyError:
            return "skill_not_installed", (
                f"Skill {skill_name!r} not installed. "
                f"Browse /skills to install it first."
            )
        req.skill_name = skill_name
        req.skill_activation = (
            f"Activating skill: **{skill_name}**\n\n"
            f"{skill_md}\n\n"
            f"---\n\n"
        )
        # allowed-tools enforcement — if the skill declares an explicit
        # allowlist, restrict the LLM's tool set for this turn to that
        # intersection. Empty list = unrestricted; matches claude-code
        # semantics.
        if resolved is not None and resolved.allowed_tools:
            req.tools_flag = list(set(resolved.allowed_tools))
    except Exception as _skill_err:
        return "skill_load_failed", f"/skill load failed: {_skill_err}"
    return None


def _skill_model_text(req: _ChatRequest) -> str | None:
    """What the model reads for an expanded ``/skill`` message, else ``None``.

    Built from the stored text so attachment paths added to the request
    after expansion reach the model too.
    """
    if not req.skill_activation:
        return None
    _, request = _split_skill_command(req.text)
    return req.skill_activation + (
        request or f"Please apply the {req.skill_name} skill."
    )
