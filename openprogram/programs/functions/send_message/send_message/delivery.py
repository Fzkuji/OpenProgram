"""Message assembly and delivery paths for send_message: the
sender-receipt header and the busy-target inbox path."""
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


def job_header(sender_session_id: str, sender_msg_id: str) -> str:
    """The receipt header prepended to a tracked-job dispatch
    (``agent(to=…)``, direct and queued-consume paths). Unlike the
    message header, it tells the receiver this turn IS the job: the
    reply is not optional chatter but the job's result, returned to
    the dispatcher automatically."""
    src = f"{sender_session_id}:{sender_msg_id}"
    return (
        f"[task from {src}] This is a tracked job dispatched to your "
        "branch via the agent tool. Do the work in this turn; your final "
        "reply is returned to the dispatcher automatically when the turn "
        "ends.\n\n"
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
    chain_messages: int,
    chain_generations: int = 0,
    authority: dict | None = None,
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
            chain_messages=chain_messages,
            chain_generations=chain_generations,
            target_head_id=branch_from,
            authority=authority,
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
