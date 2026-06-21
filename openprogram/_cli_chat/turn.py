"""One chat turn: load history, exec (with optional streaming), persist."""
from __future__ import annotations

from typing import Any, Optional


_HISTORY_CHAR_BUDGET = 60_000


def _run_turn_with_history(
    agent,
    session_id: str,
    message: str,
    *,
    console: Optional[Any] = None,
) -> str:
    """Run one CLI chat turn, persisted to
    ``<state>/agents/<agent_id>/sessions/<session_id>/``.

    Loads the session's prior messages, builds the layered system prompt
    via ``openprogram.context.system_prompt``, renders history as a
    plain-text prefix bounded by a char budget, calls rt.exec, and
    appends + saves both sides.

    Streaming: when ``console`` is provided (Rich Console), the
    assistant's reply is emitted token-by-token to the terminal via
    ``rt.on_stream``. Caller should skip a redundant final print of
    the same text. Tool calls and thinking blocks render as in-line
    one-liners so the user knows something is happening without
    losing the chat flow.

    When ``console`` is None (e.g. ``--print`` one-shot path) the
    function silently runs to completion and returns the full reply
    string — caller prints it once at the end.
    """
    import time as _time
    import uuid as _uuid
    from openprogram.agent.management import runtime_registry as _runtimes
    from openprogram.context.system_prompt import build_system_prompt
    from openprogram.webui import persistence as _persist

    data = _persist.load_session(agent.id, session_id) or {}
    meta = {k: v for k, v in data.items() if k != "messages"}
    messages: list = list(data.get("messages") or [])
    if not meta:
        # Brand-new CLI session: start with an EMPTY title. The unified
        # two-phase naming (docs/design/runtime/session/) is owned by
        # ``_maybe_auto_title`` below — it stamps the phase-1 truncated
        # placeholder and kicks off the phase-2 background LLM rename.
        # We must NOT set ``_titled`` here: that legacy lock made CLI
        # sessions opt out of LLM naming forever.
        meta = {
            "id": session_id,
            "agent_id": agent.id,
            "title": "",
            "created_at": _time.time(),
            "source": "cli",
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
        if console is not None:
            reply_text = _exec_streaming(rt, exec_content, console)
        else:
            reply = rt.exec(content=exec_content)
            reply_text = str(reply or "").strip() or ""
    except Exception as e:  # noqa: BLE001
        reply_text = f"[error] {type(e).__name__}: {e}"
        if console is not None:
            console.print(f"\n[red]{reply_text}[/]")

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

    # Unified two-phase session naming. The CLI runs each turn as a
    # bare ``rt.exec`` and never passes through the dispatcher's
    # ``finalize_turn``, so we call the same titling hook directly:
    # phase 1 stamps a truncated placeholder, phase 2 spawns a daemon
    # thread that LLM-renames. Idempotent and self-gating (skips when
    # ``_user_titled``; only fires at turn thresholds). Best-effort —
    # a CLI turn must never fail because titling did.
    try:
        from openprogram.agent.dispatcher.titles import _maybe_auto_title
        from openprogram.agent.session_db import default_db as _title_db
        _tdb = _title_db()
        _trow = _tdb.get_session(session_id) or {}
        _maybe_auto_title(_tdb, session_id, _trow, message, reply_text)
    except Exception:
        pass

    return reply_text


def _exec_streaming(rt, exec_content: list[dict], console) -> str:
    """Call ``rt.exec`` with an ``on_stream`` callback that writes text
    deltas to the terminal as they arrive.

    Returns the final reply text (same as the non-streaming path) for
    persistence. The caller has already seen the text live, so it
    must NOT print ``reply_text`` again after this returns.

    Layout per event type:

    * ``text``           → bytes streamed inline, no newline
    * ``thinking``       → dim, prefixed once with "thinking:"
                           then deltas appended inline
    * ``tool_use``       → one line, dim ``[tool: name]``
    * ``tool_result``    → suppressed (too noisy live)

    Restores ``rt.on_stream`` to ``None`` on exit so subsequent
    callers (web UI on same runtime) aren't affected.
    """
    import sys

    previous = getattr(rt, "on_stream", None)
    # Track whether we've already started printing in a non-text mode
    # so we can switch back cleanly between thinking → text → tool_use.
    state = {"mode": None}

    def _flush(text: str = ""):
        # Plain ``sys.stdout`` for streaming — Rich's Console.print
        # appends a newline on every call and runs markup parsing,
        # neither of which we want for byte-level streaming.
        sys.stdout.write(text)
        sys.stdout.flush()

    def _switch_mode(new_mode: str) -> None:
        old = state["mode"]
        if old == new_mode:
            return
        # End-of-mode newline so text from one mode doesn't run into
        # the next.
        if old in ("thinking", "text", "tool"):
            _flush("\n")
        state["mode"] = new_mode

    def _on_stream(ev):
        t = ev.get("type")
        if t == "text":
            _switch_mode("text")
            _flush(ev.get("text") or "")
        elif t == "thinking":
            if state["mode"] != "thinking":
                _switch_mode("thinking")
                _flush("\x1b[2mthinking: ")  # dim ANSI
            _flush(ev.get("text") or "")
        elif t == "tool_use":
            _switch_mode("tool")
            tool = ev.get("tool", "?")
            _flush(f"\x1b[2m[{tool}…]\x1b[0m\n")
            state["mode"] = None  # next event starts fresh
        # tool_result intentionally suppressed live — too noisy

    rt.on_stream = _on_stream
    try:
        # Blank line before the streamed reply, for breathing room
        # after the user's "❯ ..." prompt.
        _flush("\n")
        reply = rt.exec(content=exec_content)
        # If the last live event was thinking, close the dim attr.
        if state["mode"] == "thinking":
            _flush("\x1b[0m")
        # Make sure we end on a clean line.
        _flush("\n")
        return str(reply or "").strip() or ""
    finally:
        rt.on_stream = previous


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
