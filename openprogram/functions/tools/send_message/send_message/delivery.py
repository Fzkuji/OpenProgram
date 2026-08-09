"""Message assembly and delivery paths for send_message: the
sender-receipt header, the busy-target inbox path, and oversized-reply
clipping."""
from __future__ import annotations


def sender_header(sender_session_id: str, sender_msg_id: str) -> str:
    """The receipt header prepended to every message delivered to an
    existing branch (direct and queued-consume paths), so the receiver
    knows who sent it and how to answer."""
    src = f"{sender_session_id}:{sender_msg_id}"
    return (
        f"[message from {src}] To reply, use send_message(to=\"{src}\"). "
        "Replying is optional — this message is already delivered; if you "
        "have nothing substantive to add, do not reply.\n\n"
    )


def enqueue_for_busy_target(
    run_session: str,
    branch_from: str | None,
    delivery_body: str,
    *,
    sender_session_id: str,
    sender_msg_id: str,
    sender_agent_id: str | None,
    agent_id: str,
    spawn_depth: int,
) -> str | None:
    """Busy target → inbox (design §5.4: don't interrupt, don't drop —
    queue). Returns the status string to hand back to the sender, or
    None when the target is idle (caller delivers directly).

    Only cross-session sends can race another turn — a same-session
    send runs inside the sender's own turn — so the caller only invokes
    this for cross-session existing-branch deliveries.
    """
    from openprogram.agent.run_control import is_turn_running
    if not is_turn_running(run_session):
        return None
    from openprogram.agent import inbox
    try:
        status = inbox.enqueue(
            run_session,
            message=delivery_body,
            sender_session_id=sender_session_id,
            sender_msg_id=sender_msg_id,
            sender_agent_id=sender_agent_id,
            agent_id=agent_id,
            spawn_depth=spawn_depth,
            target_head_id=branch_from,
        )
    except Exception as e:  # noqa: BLE001
        return f"[send_message error] {type(e).__name__}: {e}"
    if status == "duplicate":
        return (
            "[send_message] duplicate message ignored — an "
            "identical message from you is already queued for "
            "this target (sent within the last 60s)."
        )
    # Race window: the target may have finished between the busy
    # check and the enqueue — drain now so the message doesn't
    # sit a whole extra turn.
    if not is_turn_running(run_session):
        try:
            inbox.drain(run_session)
        except Exception:
            pass
    return (
        f"[queued] target branch {run_session}:{branch_from} is "
        "busy running a turn. Your message is queued; it will be "
        "processed when the target's current turn ends and its "
        "reply will come back to you automatically."
    )


_MAX_RESULT_CHARS = 30_000


def _clip_result(text: str) -> str:
    """Truncate an oversized reply head+tail and save the full text to a
    file, returning a path the caller can read (§5.6) — so a huge branch
    reply doesn't blow up the sender's context."""
    s = text or ""
    if len(s) <= _MAX_RESULT_CHARS:
        return s
    import tempfile
    import os
    head = s[: _MAX_RESULT_CHARS // 2]
    tail = s[-_MAX_RESULT_CHARS // 2:]
    try:
        fd, path = tempfile.mkstemp(prefix="branch_reply_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(s)
    except Exception:
        path = "(could not write file)"
    return (
        f"{head}\n\n... [truncated {len(s)} chars; full reply saved to "
        f"{path}] ...\n\n{tail}"
    )
