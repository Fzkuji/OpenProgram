"""One chat turn: load history, exec, persist."""
from __future__ import annotations


_HISTORY_CHAR_BUDGET = 60_000


def _run_turn_with_history(agent, session_id: str, message: str) -> str:
    """Run one CLI chat turn, persisted to
    ``<state>/agents/<agent_id>/sessions/<session_id>/``.

    Loads the session's prior messages, builds the layered system prompt
    via ``openprogram.context.system_prompt``, renders history as a
    plain-text prefix bounded by a char budget, calls rt.exec, and
    appends + saves both sides.
    """
    import time as _time
    import uuid as _uuid
    from openprogram.agents import runtime_registry as _runtimes
    from openprogram.context.system_prompt import build_system_prompt
    from openprogram.webui import persistence as _persist

    data = _persist.load_session(agent.id, session_id) or {}
    meta = {k: v for k, v in data.items()
            if k not in ("messages", "function_trees")}
    messages: list = list(data.get("messages") or [])
    if not meta:
        meta = {
            "id": session_id,
            "agent_id": agent.id,
            "title": message[:50] + ("..." if len(message) > 50 else ""),
            "created_at": _time.time(),
            "source": "cli",
            "_titled": True,
        }

    user_id = _uuid.uuid4().hex[:12]
    user_msg = {
        "role": "user", "id": user_id,
        "parent_id": messages[-1]["id"] if messages else None,
        "content": message, "timestamp": _time.time(),
        "source": "cli", "peer_display": "you",
    }
    messages.append(user_msg)

    system_prompt = build_system_prompt(agent)
    rendered_history = _render_history_plain(messages[:-1], _HISTORY_CHAR_BUDGET)

    exec_content: list[dict] = []
    if system_prompt:
        exec_content.append({"type": "text", "text": system_prompt})
    if rendered_history:
        exec_content.append({"type": "text", "text": rendered_history})
    exec_content.append({"type": "text", "text": message})

    try:
        rt = _runtimes.get_runtime_for(agent)
        reply = rt.exec(content=exec_content)
        reply_text = str(reply or "").strip() or ""
    except Exception as e:  # noqa: BLE001
        reply_text = f"[error] {type(e).__name__}: {e}"

    reply_msg = {
        "role": "assistant", "id": user_id + "_reply",
        "parent_id": user_id,
        "content": reply_text, "timestamp": _time.time(), "source": "cli",
    }
    messages.append(reply_msg)
    meta["head_id"] = reply_msg["id"]
    meta["_last_touched"] = _time.time()

    _persist.save_meta(agent.id, session_id, meta)
    _persist.save_messages(agent.id, session_id, messages)
    return reply_text


def _render_history_plain(messages: list[dict], budget: int) -> str:
    """Render history as a text prefix from newest end, capped to
    ``budget`` chars. Drops oldest messages to fit."""
    if not messages:
        return ""
    kept: list[str] = []
    running = 0
    for m in reversed(messages):
        role = m.get("role") or "user"
        content = m.get("content") or ""
        line = f"[{role}] {content}".strip()
        if running + len(line) > budget and kept:
            break
        running += len(line) + 2
        kept.append(line)
    kept.reverse()
    return "\n\n".join(kept)
