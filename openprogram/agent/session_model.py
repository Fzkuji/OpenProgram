"""Single source of truth for which chat model a session runs.

Display, dispatch, retry, and logs must all call these helpers. A session
pin always wins. The agent default is copied onto the session at create
(or once at first send if the pin was lost) and then never re-read for
that session — that is what stopped Sol chips from silently answering
on Grok.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

_log = logging.getLogger(__name__)


def default_chat_model(agent_id: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Agent.json default. Only for *new* sessions, never for a pinned turn."""
    try:
        from openprogram.agent.management.manager import DEFAULT_AGENT_ID, get as get_agent
        spec = get_agent(agent_id or DEFAULT_AGENT_ID)
        provider = (spec.model.provider or "").strip() or None
        model = (spec.model.id or "").strip() or None
        if provider:
            return provider, model
    except Exception as exc:
        _log.warning("agent default chat model unavailable: %s", exc)
    return None, None


def read_session_chat_model(session_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return the session pin, or (None, None). Does not consult agent.json."""
    if not session_id:
        return None, None
    provider, model = _read_memory_pin(session_id)
    if provider and model:
        return provider, model
    return _read_db_pin(session_id)


def ensure_session_chat_model(
    session_id: Optional[str],
    agent_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Pin used for this turn.

    If the session already has a pin, return it. If the session exists
    but the pin was dropped, freeze the agent default onto the session
    once so chip and dispatch cannot diverge again.
    """
    provider, model = read_session_chat_model(session_id)
    if provider and model:
        return provider, model
    provider, model = default_chat_model(agent_id)
    if session_id and provider and model:
        write_session_chat_model(session_id, provider, model)
    return provider, model


def write_session_chat_model(session_id: str, provider: str, model: str) -> None:
    try:
        from openprogram.webui import server as _s
        conv = getattr(_s, "_sessions", {}).get(session_id)
        if isinstance(conv, dict):
            conv["provider_override"] = provider
            conv["model_override"] = model
    except Exception as exc:
        _log.warning("session pin memory write failed session=%s: %s", session_id, exc)
    try:
        from openprogram.agent.session_db import default_db
        default_db().update_session(
            session_id,
            provider_override=provider,
            model_override=model,
        )
    except Exception as exc:
        _log.warning("session pin persist failed session=%s: %s", session_id, exc)
        raise


def override_string(provider: Optional[str], model: Optional[str]) -> Optional[str]:
    if provider and model:
        return f"{provider}/{model}"
    return model or None


def _read_memory_pin(session_id: str) -> tuple[Optional[str], Optional[str]]:
    try:
        from openprogram.webui import server as _s
        conv = getattr(_s, "_sessions", {}).get(session_id) or {}
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
        from openprogram.agent.session_db import default_db
        meta = default_db().get_session(session_id) or {}
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
