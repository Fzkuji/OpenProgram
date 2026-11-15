"""Project provider / legacy stream events into CallStreamState.

Normalization happens here before information is lost. Legacy
``on_stream`` remains a compatibility projection and is invoked by the
caller separately when present.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .state import CallStreamState, StreamIdentity
from .transport import (
    get_display_msg_id,
    get_stream_transport,
    register_owner,
    unregister_owner,
)

_log = logging.getLogger(__name__)


def resolve_stream_identity(
    *,
    node_id: str,
    provider: str = "",
    model: str = "",
) -> Optional[StreamIdentity]:
    """Build identity from turn ContextVars + transport. None → skip."""
    if not node_id:
        return None
    transport = get_stream_transport()
    session_id = ""
    execution_id = ""
    generation = 1
    display_msg_id = get_display_msg_id()
    ephemeral = False
    if transport is not None:
        session_id = transport.session_id
        execution_id = transport.execution_id
        generation = transport.generation
        if not display_msg_id:
            display_msg_id = transport.display_msg_id
    if not session_id:
        try:
            from openprogram.runtime.execution.run_control import get_current_session_id

            session_id = get_current_session_id() or ""
        except Exception:
            session_id = ""
    if not execution_id:
        try:
            from openprogram.runtime.execution.run_control import get_current_execution_id

            execution_id = get_current_execution_id() or ""
        except Exception:
            execution_id = ""
    if not session_id or not execution_id:
        # Standalone / no turn context — local ephemeral collector only.
        ephemeral = True
        session_id = session_id or "ephemeral"
        execution_id = execution_id or "ephemeral"
    return StreamIdentity(
        session_id=session_id,
        execution_id=execution_id,
        node_id=node_id,
        generation=generation,
        display_msg_id=display_msg_id,
        ephemeral=ephemeral,
    )


def create_call_stream_state(
    *,
    node_id: str,
    provider: str = "",
    model: str = "",
) -> Optional[CallStreamState]:
    ident = resolve_stream_identity(node_id=node_id, provider=provider, model=model)
    if ident is None:
        return None
    transport = get_stream_transport()
    emit = transport.emit_envelope if transport is not None else None
    if emit is None and not ident.ephemeral:
        from .transport import make_broadcast_emit

        emit = make_broadcast_emit(ident.session_id)
    state = CallStreamState(
        ident, emit=emit, provider=provider, model=model
    )
    register_owner(state)
    return state


def release_call_stream_state(state: Optional[CallStreamState]) -> None:
    if state is None:
        return
    try:
        state.flush_checkpoint()
    except Exception:
        # Release must still unregister the owner when the final flush fails.
        _log.warning("call stream checkpoint not flushed", exc_info=True)
    unregister_owner(state)


def project_provider_event(state: CallStreamState, event: dict[str, Any]) -> None:
    """Apply one legacy-shaped or structured event onto ``state``."""
    if not isinstance(event, dict):
        return
    etype = event.get("type")
    try:
        # ``hidden`` is a lifecycle policy, not a redacted visible row. The
        # provider projection must discard it before CallStreamState can
        # create a tool_ref with a leaked name or empty DAG reference.
        if event.get("expose") == "hidden" and etype in {"tool_use", "tool_result", "tool_arguments"}:
            return
        if etype == "text":
            state.append_delta(kind="text", delta=str(event.get("text") or ""))
        elif etype == "thinking":
            state.append_delta(
                kind="reasoning_summary",
                delta=str(event.get("text") or ""),
            )
        elif etype == "structured_output_retry":
            state.finish_attempt(
                status="failed",
                validation="failed",
                error_summary=str(event.get("error") or "structured_output_retry"),
            )
            state.start_attempt(
                reason="structured_output_repair",
                provider=str(event.get("provider") or ""),
                model=str(event.get("model") or ""),
            )
        elif etype == "structured_output_end":
            validation = "passed" if not event.get("error") else "failed"
            state.finish_attempt(
                status="completed" if validation == "passed" else "failed",
                validation=validation,
            )
        elif etype == "done":
            # Provider finished producing content for this attempt; node
            # completion is decided by Runtime close.
            state.finish_block(kind="text", finish_reason="done")
            state.finish_block(kind="reasoning_summary", finish_reason="done")
        elif etype == "refusal":
            state.append_delta(
                kind="refusal", delta=str(event.get("text") or event.get("refusal") or "")
            )
            state.finish_block(kind="refusal", finish_reason="refusal")
        elif etype == "tool_use":
            # Ordered timeline: tool_ref points at the DAG tool node.
            # Args/results stay on that node — do not duplicate here.
            state.add_tool_ref(
                tool_call_id=str(event.get("tool_call_id") or ""),
                occurrence_id=str(event.get("occurrence_id") or ""),
                tool_name=str(event.get("tool") or event.get("tool_name") or ""),
                ref_node_id=str(event.get("node_id") or event.get("ref_node_id") or ""),
                group_id=str(event.get("group_id") or ""),
                status="running",
            )
        elif etype == "tool_result":
            state.finish_tool_ref(
                str(event.get("tool_call_id") or ""),
                occurrence_id=str(event.get("occurrence_id") or ""),
                ref_node_id=str(event.get("node_id") or event.get("ref_node_id") or ""),
            )
        # tool args streaming (optional preview) — still allowed but not required
        elif etype == "tool_arguments":
            text = event.get("input") if isinstance(event.get("input"), str) else str(event.get("text") or "")
            if text:
                state.append_delta(kind="tool_arguments", delta=text)
    except Exception:
        _log.debug("project_provider_event failed", exc_info=True)


def attach_call_stream_to_exec(
    exec_state,
    *,
    node_id: Optional[str],
    provider: str = "",
    model: str = "",
) -> Optional[CallStreamState]:
    """Create CallStreamState on the current exec scratch and start attempt 0."""
    if not node_id or exec_state is None:
        return None
    existing = getattr(exec_state, "call_stream", None)
    if existing is not None:
        return existing
    state = create_call_stream_state(
        node_id=node_id, provider=provider, model=model
    )
    if state is None:
        return None
    exec_state.call_stream = state
    state.start_attempt(reason="initial", provider=provider, model=model)
    state.emit_snapshot(durability="live")
    return state


def note_transport_retry(exec_state, *, reason: str = "transport_retry") -> None:
    state = getattr(exec_state, "call_stream", None) if exec_state else None
    if state is None:
        return
    state.finish_attempt(status="failed", validation="failed", error_summary=reason)
    state.start_attempt(reason=reason)


def finalize_call_stream(
    exec_state,
    *,
    status: str = "completed",
    result: Any = None,
    reason_code: str = "",
) -> Optional[dict]:
    """Finish node stream; return metadata.stream dict for DAG write."""
    state = getattr(exec_state, "call_stream", None) if exec_state else None
    if state is None:
        return None
    mapped = (
        "cancelled"
        if status == "cancelled"
        else "failed"
        if status in ("error", "failed")
        else "completed"
    )
    if mapped == "cancelled":
        state.stop_accepting()
    meta = state.finish_node(
        status=mapped,
        result=result if mapped == "completed" else None,
        reason_code=reason_code or mapped,
        validation="passed" if mapped == "completed" else "failed",
    )
    release_call_stream_state(state)
    try:
        exec_state.call_stream = None
    except Exception:
        # Detaching is cleanup; the node already carries its final status.
        _log.debug("call stream not detached from exec state", exc_info=True)
    return meta
