"""Push a function's runtime.ask / form question into the chat it came
from, so a channel user can answer it with /answer (see
_question_commands).

How it works — the non-blocking half of the channel Q&A loop:
  - A function paused on runtime.ask emits ``question.asked`` on the
    process event bus (fire-and-forget, NOT on the blocked call stack).
  - This bridge subscribes to that event. From the question's
    ``session_id`` it looks up the session's channel binding
    (channel / account_id / peer) in SessionDB; if the session came from
    a channel, it renders the question as plain text and sends it there.
  - The blocked function keeps waiting; when the user replies /answer,
    _question_commands resolves the registry and the function resumes.
    Because the send happens on the bus subscriber (a different thread
    from the blocked function), there is no self-deadlock — provided the
    channel's own receive loop isn't serialized behind the blocked call
    (telegram/wechat run dispatch per-message in a thread for this).

Questions whose session has no channel binding (web UI / ad-hoc) are
ignored here — the web card path already covers them.

Plain text only for now; clickable buttons (telegram inline keyboard,
discord components) are a later refinement. The text tells the user the
exact /answer command to send.
"""
from __future__ import annotations

from typing import Any

_installed = False


def install_question_bridge() -> None:
    """Subscribe the bridge to the process event bus. Idempotent — safe to
    call once at worker startup."""
    global _installed
    if _installed:
        return
    try:
        from openprogram.agent.event_bus import get_event_bus
    except Exception:
        return
    get_event_bus().subscribe(_on_question_asked, types={"question.asked"})
    _installed = True


def _on_question_asked(event) -> None:
    try:
        data = getattr(event, "payload", None) or {}
        session_id = data.get("session_id") or (
            getattr(event, "metadata", None) or {}).get("session") or ""
        if not session_id:
            return
        target = _channel_target_for_session(session_id)
        if target is None:
            return  # not a channel session (web / ad-hoc) — web card covers it
        channel, account_id, peer_id = target
        text = _render_question(data)
        from openprogram.channels.outbound import send as _send
        _send(channel, account_id, peer_id, text)
    except Exception:
        # Never let a rendering / send hiccup crash the bus dispatch.
        pass


def _channel_target_for_session(session_id: str):
    """(channel, account_id, peer_id) for a channel-bound session, else None."""
    try:
        from openprogram.agent.session_db import default_db
        sess = default_db().get_session(session_id)
    except Exception:
        sess = None
    if not sess:
        return None
    channel = sess.get("channel") or sess.get("source")
    account_id = sess.get("account_id")
    peer_id = sess.get("peer_id")
    if not peer_id:
        peer = sess.get("peer") or {}
        peer_id = peer.get("id") if isinstance(peer, dict) else None
    if not channel or not peer_id:
        return None
    return str(channel), str(account_id or ""), str(peer_id)


def _render_question(data: dict[str, Any]) -> str:
    """Render a pending question as a plain-text chat message that tells
    the user the exact /answer command to reply with."""
    qid = data.get("id") or "?"
    kind = data.get("kind") or "ask"
    prompt = (data.get("prompt") or "").strip()
    detail = (data.get("detail") or "").strip()
    options = list(data.get("options") or [])
    multi = bool(data.get("multi"))
    schema = data.get("schema") or {}

    lines: list[str] = []
    head = {"confirm": "需要确认", "approval": "⚠ 需要批准", "form": "需要填写"}.get(kind, "需要你的回答")
    lines.append(f"🤔 {head}")
    if prompt:
        lines.append(prompt)
    if detail:
        lines.append(detail)

    if kind == "form" and isinstance(schema, dict) and schema:
        # Multi-field form: list the fields, ask for a JSON reply.
        lines.append("")
        lines.append("字段：")
        for name, f in schema.items():
            f = f or {}
            label = f.get("title") or name
            extra = ""
            if f.get("enum"):
                extra = f" ({'/'.join(str(x) for x in f['enum'])})"
            lines.append(f"  • {label}{extra}")
        lines.append("")
        lines.append(f'回复：/answer {qid} {{"字段名":"值", …}}')
        return "\n".join(lines)

    if options:
        lines.append("")
        for i, opt in enumerate(options, start=1):
            lines.append(f"  {i}) {opt}")
        lines.append("")
        if multi:
            lines.append(f"回复（可多选，逗号分隔）：/answer {qid} 1,2")
        else:
            lines.append(f"回复：/answer {qid} 1   （或直接输入答案）")
    else:
        lines.append("")
        lines.append(f"回复：/answer {qid} 你的回答")
    lines.append(f"拒绝：/decline {qid}")
    return "\n".join(lines)
