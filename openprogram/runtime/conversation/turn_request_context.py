"""The TurnRequest in force, plus the owner's original input.

The request in force and the inner request derived from it are call-core
state: import them from ``openprogram.runtime.agent.turn_request``.
This module adds what only the conversation engine knows: the input an
admitted owner turn started from (``OriginalOwnerInput``), captured before a
tool runs in another process and validated against the execution store.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any, Optional

# The request in force is call-core state; this module adds the admitted
# owner's original input on top of it.
from openprogram.runtime.agent import turn_request as _turn_request


@dataclass(frozen=True)
class OriginalOwnerInput:
    """Source data from an admitted human turn, never execution authority."""

    session_id: str
    user_msg_id: str
    user_text: str
    principal_id: str
    session_dir: str
    execution_id: str
    execution_store: str
    speaker_kind: str
    speaker_id: str
    speaker_display: str
    authority_tier: str
    interaction: str


_original_owner_input: contextvars.ContextVar[Optional[OriginalOwnerInput]] = (
    contextvars.ContextVar("openprogram_original_owner_input", default=None)
)


def set_original_owner_input(value: Optional[OriginalOwnerInput]):
    return _original_owner_input.set(value)


def reset_original_owner_input(token) -> None:
    _original_owner_input.reset(token)


def _valid_original_input(value: OriginalOwnerInput, request: Any) -> bool:
    from openprogram.state.authority import normalize_authority
    from openprogram.runtime.execution.run_control import get_current_execution_id
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.execution import default_store

    authority = normalize_authority(request)
    source_authority = normalize_authority(value)
    if (not isinstance(value, OriginalOwnerInput)
            or source_authority.get("speaker_kind") != "owner"
            or source_authority.get("authority_tier") != "owner"
            or authority.get("authority_tier") != "owner"
            or (authority.get("speaker_kind"), authority.get("interaction"))
                not in {("owner", "interactive"), ("runtime", "non-interactive")}
            or value.session_id != getattr(request, "session_id", None)
            or value.principal_id != getattr(request, "principal_id", None)
            or value.execution_id != get_current_execution_id()):
        return False
    try:
        db, store = default_db(), default_store()
        if (str(db._session_dir(value.session_id).resolve()) != value.session_dir
                or str(store.path.resolve()) != value.execution_store):
            return False
        admitted = store.get_execution_input(value.execution_id)
        payload = store.get_agent_turn_input(value.execution_id)
    except Exception:
        # Missing durable input cannot become original evidence.
        return False
    actor = normalize_authority(admitted.trusted_actor) if admitted else {}
    return bool(admitted and payload and payload.get("kind") == "chat"
                and admitted.session_id == value.session_id
                and admitted.user_message_id == value.user_msg_id
                and actor == source_authority
                and payload.get("request", {}).get("user_text") == value.user_text)


def original_owner_request() -> Optional[Any]:
    """Resolve only the current admitted input; leave runtime authority intact."""
    from openprogram.state.authority import normalize_authority

    request = _turn_request.get_turn_request()
    authority = normalize_authority(request)
    if authority.get("speaker_kind") == "owner" and authority.get("authority_tier") == "owner":
        return request
    value = _original_owner_input.get()
    return value if value is not None and _valid_original_input(value, request) else None


def capture_original_owner_input(request: Any) -> Optional[OriginalOwnerInput]:
    """Freeze trusted parent context before dispatching to another process."""
    from openprogram.state.authority import normalize_authority
    from openprogram.runtime.execution.run_control import get_current_execution_id
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.execution import default_store
    from openprogram.programs._authoring.reports.sources import original_owner
    from openprogram.state.store.session.transcript import MAX_TEXT_CHARS

    authority = normalize_authority(request)
    if authority.get("speaker_kind") != "owner" or authority.get("authority_tier") != "owner":
        value = _original_owner_input.get()
        return value if value is not None and _valid_original_input(value, request) else None
    sid, mid = getattr(request, "session_id", ""), getattr(request, "user_msg_id", "")
    eid = get_current_execution_id()
    if not sid or not mid or not eid:
        return None
    db, store = default_db(), default_store()
    node = next((m for m in db.get_messages(sid) if m.get("id") == mid), None)
    if (not node or not original_owner(node)
            or not isinstance(node.get("content"), str)
            or len(node["content"]) > MAX_TEXT_CHARS
            or node.get("content") != getattr(request, "user_text", None)
            or node.get("principal_id") != authority["principal_id"]):
        return None
    value = OriginalOwnerInput(session_id=sid, user_msg_id=mid, user_text=node["content"],
        session_dir=str(db._session_dir(sid).resolve()), execution_id=eid,
        execution_store=str(store.path.resolve()), **authority)
    return value if _valid_original_input(value, request) else None


__all__ = [
    "OriginalOwnerInput",
    "capture_original_owner_input",
    "original_owner_request",
    "reset_original_owner_input",
    "set_original_owner_input",
]
