"""One chat turn: load history, exec, persist."""
from __future__ import annotations


def _run_turn_with_history(agent, session_id: str, message: str) -> str:
    """Run one CLI chat turn, persisted to
    ``<state>/agents/<agent_id>/sessions/<session_id>/``.

    Loads the session's prior messages, runs them through the per-agent
    context engine, calls rt.exec, and appends + saves both sides.
    """
    import time as _time
    import uuid as _uuid
    from openprogram.agents import runtime_registry as _runtimes
    from openprogram.agents.context_engine import default_engine as _engine
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
    _engine.ingest(messages, user_msg)

    assembled = _engine.assemble(agent, meta, messages[:-1])
    exec_content: list[dict] = []
    if assembled.system_prompt_addition:
        exec_content.append({
            "type": "text", "text": assembled.system_prompt_addition,
        })
    exec_content.extend(assembled.messages)
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
    _engine.ingest(messages, reply_msg)
    _engine.after_turn(agent, meta, messages)
    meta["head_id"] = reply_msg["id"]
    meta["_last_touched"] = _time.time()

    _persist.save_meta(agent.id, session_id, meta)
    _persist.save_messages(agent.id, session_id, messages)
    return reply_text
