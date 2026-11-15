"""Authorized transport for execution_stream envelopes.

In-process: emit goes to the turn's ``on_event`` / WS broadcast.
Subprocess: child puts envelopes on ``event_queue``; parent validates
identity against the launch record, then re-broadcasts. Parent never
reallocates revision or writes the child's node checkpoint.
"""
from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .protocol import SCHEMA_VERSION, STREAM_OPS

_log = logging.getLogger(__name__)

EmitFn = Callable[[dict[str, Any]], None]

_stream_transport: contextvars.ContextVar[Optional["ExecutionStreamTransport"]] = (
    contextvars.ContextVar("_execution_stream_transport", default=None)
)
_display_msg_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_execution_stream_display_msg_id", default=""
)

# Registry of live CallStreamState owners keyed by (session, execution, node).
# Used for subscribe snapshot serving within the same process.
_owners: dict[tuple[str, str, str], Any] = {}
_owners_lock = __import__("threading").Lock()


@dataclass
class ExecutionStreamTransport:
    """Bound session/execution identity for emit + parent-side validation."""

    session_id: str
    execution_id: str
    generation: int = 1
    display_msg_id: str = ""
    emit: Optional[EmitFn] = None
    # Parent-only: allowed node ids from the launch record (empty = any
    # node under this session/execution is accepted after identity check).
    allowed_node_ids: set[str] = field(default_factory=set)
    is_parent_forwarder: bool = False

    def emit_envelope(self, env: dict[str, Any]) -> None:
        if self.emit is None:
            return
        try:
            self.emit(env)
        except Exception:
            _log.debug("execution_stream emit failed", exc_info=True)


def set_stream_transport(
    transport: Optional[ExecutionStreamTransport],
):
    return _stream_transport.set(transport)


def get_stream_transport() -> Optional[ExecutionStreamTransport]:
    return _stream_transport.get()


def reset_stream_transport(token) -> None:
    try:
        _stream_transport.reset(token)
    except (ValueError, RuntimeError):
        pass


def set_display_msg_id(msg_id: str):
    return _display_msg_id.set(msg_id or "")


def get_display_msg_id() -> str:
    return _display_msg_id.get() or ""


def reset_display_msg_id(token) -> None:
    try:
        _display_msg_id.reset(token)
    except (ValueError, RuntimeError):
        pass


def register_owner(state) -> None:
    ident = state.identity
    key = (ident.session_id, ident.execution_id, ident.node_id)
    with _owners_lock:
        _owners[key] = state


def unregister_owner(state) -> None:
    ident = state.identity
    key = (ident.session_id, ident.execution_id, ident.node_id)
    with _owners_lock:
        if _owners.get(key) is state:
            _owners.pop(key, None)


def lookup_owner(
    session_id: str, execution_id: str, node_id: str
):
    with _owners_lock:
        return _owners.get((session_id, execution_id, node_id))


def make_emit_from_on_event(on_event: EmitFn) -> EmitFn:
    """Adapt dispatcher ``on_event(env)`` into a stream emit callable."""

    def _emit(env: dict[str, Any]) -> None:
        on_event(env)

    return _emit


def make_broadcast_emit(session_id: str) -> EmitFn:
    """In-process emit that fans out via the webui broadcast helper."""

    def _emit(env: dict[str, Any]) -> None:
        data = env.get("data") if isinstance(env, dict) else None
        if not isinstance(data, dict):
            return
        msg_id = (
            data.get("display_msg_id")
            or data.get("node_id")
            or ""
        )
        try:
            from openprogram.runtime import interface_link

            interface_link.broadcast_chat_response(session_id, msg_id, data)
        except Exception:
            _log.debug("broadcast emit unavailable", exc_info=True)

    return _emit


def validate_and_forward_envelope(
    env: dict[str, Any],
    *,
    session_id: str,
    execution_id: str,
    generation: Optional[int] = None,
    on_event: Optional[EmitFn] = None,
) -> bool:
    """Parent-side validation of a child-originated envelope.

    Returns True if forwarded. Rejects envelopes that widen authorization
    (wrong session/execution) or carry unknown ops / schema.
    """
    if not isinstance(env, dict):
        return False
    if env.get("type") != "chat_response":
        # Not an execution_stream frame — leave to other handlers.
        return False
    data = env.get("data")
    if not isinstance(data, dict) or data.get("type") != "execution_stream":
        return False
    if int(data.get("schema_version") or 0) != SCHEMA_VERSION:
        _log.debug("reject execution_stream: schema_version")
        return False
    op = data.get("op")
    if op not in STREAM_OPS:
        _log.debug("reject execution_stream: unknown op %r", op)
        return False
    if str(data.get("session_id") or "") != str(session_id):
        _log.warning("reject execution_stream: session mismatch")
        return False
    if str(data.get("execution_id") or "") != str(execution_id):
        _log.warning("reject execution_stream: execution mismatch")
        return False
    if generation is not None:
        try:
            if int(data.get("generation") or 0) > int(generation):
                # Child cannot invent a higher generation than launch record.
                _log.warning("reject execution_stream: generation ahead of owner")
                return False
        except (TypeError, ValueError):
            return False
    node_id = data.get("node_id")
    if not node_id or not isinstance(node_id, str):
        return False
    # Bound size: drop absurd frames.
    try:
        import json as _json

        raw = _json.dumps(data, ensure_ascii=False, default=str)
        if len(raw.encode("utf-8")) > 96 * 1024:
            _log.warning("reject execution_stream: frame too large")
            return False
    except Exception:
        return False
    if on_event is not None:
        try:
            on_event(env)
        except Exception:
            _log.debug("forward execution_stream failed", exc_info=True)
            return False
        return True
    # Direct broadcast when no on_event (in-process parent without queue).
    try:
        from openprogram.runtime import interface_link

        interface_link.broadcast_chat_response(
            session_id,
            data.get("display_msg_id") or node_id,
            data,
        )
        return True
    except Exception:
        return False


def bind_transport_for_turn(
    *,
    session_id: str,
    execution_id: str,
    generation: int = 1,
    display_msg_id: str = "",
    on_event: Optional[EmitFn] = None,
) -> tuple[Any, Any]:
    """Install transport + display_msg_id ContextVars. Returns tokens."""
    if on_event is not None:
        emit = make_emit_from_on_event(on_event)
    else:
        emit = make_broadcast_emit(session_id)
    transport = ExecutionStreamTransport(
        session_id=session_id,
        execution_id=execution_id,
        generation=generation,
        display_msg_id=display_msg_id,
        emit=emit,
    )
    t1 = set_stream_transport(transport)
    t2 = set_display_msg_id(display_msg_id)
    return t1, t2
