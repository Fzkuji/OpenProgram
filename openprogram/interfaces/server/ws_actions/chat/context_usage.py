"""Publishing a provider request's measured context size."""
from __future__ import annotations


def _record_context_usage(session_id: str, msg_id: str, conv: dict,
                          usage: dict, *, refresh_head: bool = False,
                          model: str | None = None,
                          context_window: int | None = None) -> None:
    """Publish and persist a provider request's measured prompt size.

    ``model`` (``provider/model``) and ``context_window`` are what the turn
    resolved for that request; the window shown is that Model's, the same
    one compaction reads. They are kept on the conversation, so the turn's
    final report and later refreshes use the same model. ``refresh_head``
    marks the report of a finished turn; without it the turn is still
    running.
    """
    if model and conv is not None:
        conv["_turn_model_ref"] = model
        conv["_turn_context_window"] = int(context_window or 0) or None
    model = model or (conv or {}).get("_turn_model_ref")
    if model and not context_window and model == (conv or {}).get("_turn_model_ref"):
        context_window = (conv or {}).get("_turn_context_window")
    measured = int(usage.get("context_tokens") or 0) if usage else 0
    if usage and "context_tokens" not in usage:
        measured = (int(usage.get("input_tokens") or 0)
                    + int(usage.get("cache_read_tokens") or 0))
    if not measured:
        from openprogram.interfaces.server import server as _s
        _s.refresh_context_stats(session_id, msg_id)
        return
    from types import SimpleNamespace
    from openprogram.runtime.context.session_db import default_db
    from openprogram.interfaces.server import server as _s

    if refresh_head:
        session = default_db().get_session(session_id) or {}
        with _s._sessions_lock:
            current = _s._sessions.get(session_id)
            if current is None:
                return
            current["head_id"] = session.get("head_id")
        _s._hydrate_messages_from_db(session_id)
    measured_runtime = SimpleNamespace(
        model=model,
        context_window=context_window,
        running_turn=not refresh_head,
        last_usage={
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read": int(usage.get("cache_read_tokens") or 0),
            "cache_create": int(usage.get("cache_write_tokens") or 0),
            "context_tokens": measured,
            "request_input_estimate": int(usage.get("request_input_estimate") or 0),
        },
    )
    _s._broadcast_context_stats(session_id, msg_id, chat_runtime=measured_runtime)
    _s._save_session(session_id)


def _record_canonical_context_stats(session_id: str, msg_id: str, conv: dict,
                                    turn_result) -> None:
    """Refresh HEAD and publish the last request after a completed turn."""
    _record_context_usage(
        session_id, msg_id, conv,
        getattr(turn_result, "usage", None) or {}, refresh_head=True,
    )
