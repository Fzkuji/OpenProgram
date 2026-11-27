"""Single source of truth for which chat model a session runs.

Display, dispatch, retry, and logs must all call these helpers. A branch's
own model (the person picked one for it) always wins. A branch that has
none follows its Agent: each turn reads the Agent's model as it is then, so
editing the Agent moves every conversation that did not pick its own. Only
when the Agent has no model does the model the person ran last apply.
Nothing here writes a model onto a branch; only the person's pick does.
There is no default model: with nothing used yet, a new session has no
model until the user picks one.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

_log = logging.getLogger(__name__)


def new_session_chat_model(agent_id: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Model a *new* session starts on; never consulted for a pinned turn.

    The Agent's own model when it has one, else (for a utility-tier Agent,
    ``builtin_agents.UTILITY_TIER``) the Lightweight helper's model, else the
    last used model, else nothing.
    """
    try:
        from openprogram.runtime.agent_registry.manager import DEFAULT_AGENT_ID, get as get_agent
        spec = get_agent(agent_id or DEFAULT_AGENT_ID)
        provider = (spec.model.provider or "").strip() or None if spec else None
        model = (spec.model.id or "").strip() or None if spec else None
        if provider and model:
            return provider, model
        # A scout (Explorer) without a model of its own runs on the utility
        # tier: the Lightweight helper's model, when that has one.
        from openprogram.runtime.agent_registry.builtin_agents import utility_tier_model
        pair = utility_tier_model(spec) if spec else None
        if pair:
            return pair
    except Exception as exc:
        _log.warning("agent chat model unavailable: %s", exc)
    try:
        from openprogram.models.providers.catalog.last_used import read_last_used_model
        pair = read_last_used_model()
        if pair:
            return pair
    except Exception as exc:
        _log.warning("last used model unavailable: %s", exc)
    return None, None


def read_session_chat_model(session_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return the session pin, or (None, None). Does not consult agent.json.

    A pin on a withdrawn provider reads as no pin: the turn falls back to
    the Agent's model or the last used one, as for an unknown provider."""
    provider, model = _session_pin(session_id)
    from openprogram.models.auth.provider_policy import is_withdrawn
    if is_withdrawn(provider or ""):
        return None, None
    return provider, model


def _session_pin(session_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not session_id:
        return None, None
    from openprogram.runtime.agent.turn_request import get_turn_request
    request = get_turn_request()
    if request is not None and getattr(request, "session_id", None) == session_id:
        override = getattr(request, "model_override", None)
        if override:
            from openprogram.models.providers.catalog.models import split_model_ref
            return split_model_ref(override)
        if getattr(request, "branch_id", None):
            from openprogram.runtime.context.session_db import default_db
            from openprogram.state.store.session.branch_config import config_for_branch
            config = config_for_branch(default_db().get_session(session_id) or {}, request.branch_id)
            return config.get("provider_override"), config.get("model_override")
    from openprogram.runtime.context import Context
    context = Context.current()
    if context is not None and context.store is not None:
        return _read_db_pin(session_id)
    provider, model = _read_memory_pin(session_id)
    if provider and model:
        return provider, model
    return _read_db_pin(session_id)


def ensure_session_chat_model(
    session_id: Optional[str],
    agent_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """The model used for this turn.

    The branch's own model when it has one; else the Agent's model as it
    is now, else the model the person ran last (``global``). Never written
    onto the branch: a branch without its own model keeps following its
    Agent (branches B1). Inside a turn the answer is kept on the request,
    so the turn and its nested calls agree on it.
    """
    provider, model = read_session_chat_model(session_id)
    if provider and model:
        return provider, model
    provider, model = new_session_chat_model(agent_id)
    from openprogram.runtime.agent.turn_request import get_turn_request
    request = get_turn_request()
    if request is not None and getattr(request, "session_id", None) == session_id:
        request.model_override = override_string(provider, model)
    return provider, model


def write_session_chat_model(
    session_id: str, provider: str, model: str, *, agent_id: Optional[str] = None,
) -> None:
    """Pin a session's chat model.

    Pass ``agent_id`` only when this write creates the session's first
    record, so that record names its Agent from the start; on an existing
    session it would rebind the Agent.
    """
    from openprogram.runtime.context import Context
    if read_session_chat_model(session_id) not in ((None, None), (provider, model)):
        # Another model reads none of the cache the last one wrote.
        from openprogram.models.usage.prefix_fingerprint import expect_break
        expect_break(session_id, "model_switch")
    context = Context.current()
    try:
        if context is None or context.store is None:
            from openprogram.runtime import interface_link
            conv = interface_link.live_session(session_id)
            if isinstance(conv, dict):
                conv["provider_override"] = provider
                conv["model_override"] = model
    except Exception as exc:
        _log.warning("session pin memory write failed session=%s: %s", session_id, exc)
    try:
        Context.turn_store(session_id).update_session(
            session_id,
            provider_override=provider,
            model_override=model,
            agent_id=agent_id,
        )
    except Exception as exc:
        _log.warning("session pin persist failed session=%s: %s", session_id, exc)
        raise


def override_string(provider: Optional[str], model: Optional[str]) -> Optional[str]:
    """``provider/model``; ``catalog.models.split_model_ref`` reads it back."""
    if provider and model:
        return f"{provider}/{model}"
    return model or None


def _read_memory_pin(session_id: str) -> tuple[Optional[str], Optional[str]]:
    try:
        from openprogram.runtime import interface_link
        conv = interface_link.live_session(session_id) or {}
        provider = conv.get("provider_override")
        model = conv.get("model_override")
        if provider:
            return provider, model
        if model:
            return None, model
    except Exception as exc:
        _log.warning("session pin memory read failed session=%s: %s", session_id, exc)
    return None, None


def _read_db_pin(session_id: str) -> tuple[Optional[str], Optional[str]]:
    try:
        from openprogram.runtime.context import Context
        meta = Context.turn_store(session_id).get_session(session_id) or {}
        extra = meta.get("extra_meta") or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        if not isinstance(extra, dict):
            extra = {}
        provider = meta.get("provider_override") or extra.get("provider_override")
        model = meta.get("model_override") or extra.get("model_override")
        if provider:
            return provider, model
        if model:
            return None, model
    except Exception as exc:
        _log.warning("session pin db read failed session=%s: %s", session_id, exc)
    return None, None
