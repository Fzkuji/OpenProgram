"""DeepSeek Harness's compactions, put on the model picked for them.

After yetone/magpie ``internal/gateway/dsh_compact.go`` and
``compacting.go`` (MIT License, Copyright (c) yetone). dsh compacts its
context on the model of the session's latest request, so a switch away from
a model whose provider is out of its limit compacts on that model first and
fails with it (magpie #5395). Its own setting for the model lives in each
agent preset's whole config, which a profile patch can only replace whole;
so the gateway moves each of dsh's compactions to the model picked in dsh's
Compaction Model field (``agents/dsh.py``), read on each request.

A compaction is told by dsh's ``x-deepseek-harness-compact`` mark, or, on
llm-pi-ai's routes that send none, by compaction-basic's instruction opening
a text part of the last user message.
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

AGENT = "dsh"
FIELD = "compact_model"
HEADER = "x-deepseek-harness-compact"
#: compaction-basic's COMPACTION_INSTRUCTION, a user message after the conversation.
ASK = "You are now acting as a compaction engine for this AI coding assistant."
#: How far into a text part the ask may begin (magpie ``compactLead``).
LEAD = 2048


def _marked(headers: Any) -> bool:
    value = str((headers.get(HEADER) if headers is not None else "") or "").strip().lower()
    return value not in ("", "0", "false", "no", "off")


def _texts(message: Any) -> list[str]:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return [content]
    return [str(getattr(part, "text", "") or "") for part in content or [] if getattr(part, "type", "") == "text"]


def compacting(headers: Any, context: Any) -> bool:
    """The request is dsh compacting its conversation."""
    if _marked(headers):
        return True
    for message in reversed(list(getattr(context, "messages", None) or [])):
        if getattr(message, "role", "") != "user":
            continue
        return any(0 <= text.find(ASK) <= LEAD for text in _texts(message))
    return False


def picked() -> str:
    """The model dsh's compactions go on, ``""`` for the one each asks for."""
    from . import stash
    from .agents.model_rows import unprefixed
    try:
        record = stash.entry(AGENT) or {}
    except stash.StashUnreadable:
        return ""
    return unprefixed(str((record.get("values") or {}).get(FIELD) or "").strip())


def compact_to(agent: str, headers: Any, context: Any, model: Any) -> Any:
    """The model a request goes on: the one picked for dsh's compactions
    when it is one, else ``model``. One not served now leaves the compaction
    where it asked, and the log says why (magpie ``dshCompactTo``)."""
    if agent != AGENT:
        return model
    to = picked()
    if not to:
        return model
    from .conversion import GatewayError, enabled_model_refs, model_ref, resolve_model
    if to == model_ref(model) or not compacting(headers, context):
        return model
    if not any(row.get("id") == to for row in enabled_model_refs()):
        _log.info("gateway: DeepSeek Harness's compaction stays on %s, not %s: it is not enabled",
                  model_ref(model), to)
        return model
    try:
        return resolve_model(to)
    except GatewayError as exc:
        _log.info("gateway: DeepSeek Harness's compaction stays on %s, not %s: %s", model_ref(model), to, exc)
        return model
