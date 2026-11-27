"""How a message reaches an existing agent: always as a tracked Job.

Every ``send_message`` admits a Job on the target branch and returns its
``execution_id`` (this was ``agent(to=…)``'s path; the untracked message
form is gone, because tracking is what lets a Worker restart recover the
delivery and the reply). The target's final reply comes back to the sender
as a notice turn, which has no timeout, so a reply is never dropped while
the sender is busy: it waits in the sender's ``notices.json`` until the
sender is free (OpenCode pushes a child's result the same way).

A busy target never gets interrupted: the Job is admitted first, its inbox
entry travels on the Job (``Job.deferred_inbox``), and the target runs it
when its current turn ends.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def deliver(
    *,
    message: str,
    sid: str,
    aid: str,
    parent_agent: str | None,
    chosen_agent: str,
    run_session: str,
    target_tip: str,
    caller_authority: dict,
    messages: int,
    generations: int,
    subject: str = "",
    job_id: str | None = None,
    ancestors: list[str] | None = None,
) -> str:
    """Admit the delivery Job and start or queue it; the tool's result text.

    ``job_id``: a fixed id, so delivering the same thing twice (a migrated
    hand-off after a crash) admits one Job. ``ancestors``: who waits above
    this message (send_message's cycle check), carried by the Job."""
    from openprogram.runtime.conversation.peer_agents import inbox
    from openprogram.state.authority import normalize_authority
    from .delivery import sender_header, sender_name, target_label


    # Named after the target branch, so the reply notice says who answered.
    label = target_label(run_session, target_tip, sid)
    from_name = sender_name(sid, run_session)
    delivery_message = sender_header(
        sid, aid, from_name, other_conversation=run_session != sid) + message
    ancestors = list(ancestors or []) or None

    # An empty tip is a conversation with no turn yet: the message starts it.
    tip = target_tip or None
    busy = inbox.target_busy(run_session, tip, sid)
    if busy:
        steered = _steer_own_helper(
            message=message, delivery_message=delivery_message, sid=sid, aid=aid,
            parent_agent=parent_agent, chosen_agent=chosen_agent, run_session=run_session,
            target_tip=target_tip, caller_authority=caller_authority, messages=messages,
            generations=generations, from_name=from_name, label=label, ancestors=ancestors,
        )
        if steered is not None:
            return steered
    if busy:
        from openprogram.runtime.jobs import get_runner
        from openprogram.runtime.jobs.runner import _current_job_id
        from openprogram.runtime.jobs.types import Job, JobStatus, mint_job_id
        job_id = job_id or mint_job_id()
        # The inbox entry travels on the Job from admission on, so a Worker
        # that dies before (or after) queuing it recreates it exactly once.
        intent = {
            "message": message,
            "sender_session_id": sid,
            "sender_msg_id": aid,
            "sender_agent_id": parent_agent,
            "agent_id": chosen_agent,
            "chain_messages": messages,
            "chain_generations": generations,
            "target_head_id": target_tip,
            "job_id": job_id,
            "tracked_job": False,
            "authority": caller_authority,
            "scope": "session" if run_session != sid else "branch",
            "sender_name": from_name,
            "label": label,
            "chain_ancestors": ancestors,
        }
        job = Job(
            id=job_id,
            parent_session_id=run_session,
            prompt=delivery_message,
            agent_id=chosen_agent,
            subject=subject or message[:60],
            description=delivery_message,
            context_mode="inherit" if tip else "clean",
            parent_msg_id=tip,
            parent_job_id=_current_job_id.get(),
            label=label or None,
            wait=False,
            caller_msg_id=aid,
            caller_session_id=sid,
            creates_agent=False,
            relation="linked",
            origin_turn_id=aid,
            message_origin="agent_delivery",
            chain_messages=messages + 1,
            chain_generations=generations,
            caller_chain_generations=generations,
            deferred_inbox=intent,
            chain_ancestors=ancestors,
            status=JobStatus.PENDING,
            **normalize_authority(caller_authority),
        )
        runner = get_runner()
        try:
            runner.admit_job_entity(
                job, creates_agent=False, caller_turn_id=aid, dispatch_ready=False,
            )
        except Exception as e:  # noqa: BLE001
            return f"[send_message error] {type(e).__name__}: {e}"
        try:
            queued = inbox.enqueue(run_session, **intent)
        except Exception as e:  # noqa: BLE001
            try:
                # Admitted canonically before the inbox write: cancel that
                # exact execution so every record converges on one outcome.
                runner.cancel_execution(job.id, reason=f"inbox enqueue failed: {e}")
            except Exception as cancel_error:  # noqa: BLE001
                return (f"[send_message error] {type(e).__name__}: {e}; cleanup failed: "
                        f"{type(cancel_error).__name__}: {cancel_error}")
            return f"[send_message error] {type(e).__name__}: {e}"
        if queued == "duplicate":
            try:
                runner.cancel_execution(job.id, reason="duplicate message")
            except Exception as cancel_error:  # noqa: BLE001
                return ("[send_message error] duplicate message cleanup failed: "
                        f"{type(cancel_error).__name__}: {cancel_error}")
            return ("[send_message] duplicate message ignored — an identical message "
                    "from you is already queued for this target (sent within the last 60s).")
        hold_for_relay(job)
        # Race window: the target may have finished between the busy check
        # and the enqueue; drain runs only what is ready.
        try:
            inbox.drain(run_session)
        except Exception:
            _log.debug("inbox drain failed for session %s", run_session, exc_info=True)
        return (
            f"[queued] execution_id={job.id} target={run_session}:{target_tip}\n"
            "The target is busy; your message runs when its current turn ends. "
            "Its reply comes back to you automatically. You are not blocked — continue."
        )

    from openprogram.runtime.events import emit_safe
    emit_safe("branch.message_sent", "agent",
              {"from": f"{sid}:{aid}", "to": f"{run_session}:{target_tip}"})
    try:
        from openprogram.runtime.conversation.peer_agents.sub_agent_run import run_agent_turn_async
        job_id = run_agent_turn_async(
            session_id=run_session,
            prompt=delivery_message,
            agent_id=chosen_agent,
            branch_from=tip,
            context_mode="inherit" if tip else "clean",
            label=label or None,
            subject=subject or message[:60],
            description=delivery_message,
            caller_msg_id=aid,
            caller_session_id=sid,
            chain_messages=messages + 1,
            # No agent is created, so the target and the reply turn both
            # run at the generation count this send was made at.
            chain_generations=generations,
            caller_chain_generations=generations,
            authority=caller_authority,
            creates_agent=False,
            message_origin="agent_delivery",
            job_id=job_id,
            chain_ancestors=ancestors,
        )
    except Exception as e:  # noqa: BLE001
        return f"[send_message error] {type(e).__name__}: {e}"
    hold_for_relay(job_id)
    return (
        f"[delivered] execution_id={job_id} target={run_session}:{target_tip}\n"
        "The target is running your message as its next turn; its reply comes "
        "back to you automatically when it finishes. You are not blocked — "
        "continue, or end your turn to wait."
    )


def _steer_own_helper(
    *, message: str, delivery_message: str, sid: str, aid: str, parent_agent: str | None,
    chosen_agent: str, run_session: str, target_tip: str, caller_authority: dict,
    messages: int, generations: int, from_name: str, label: str,
    ancestors: list[str] | None,
) -> str | None:
    """A message to a running helper this turn started reaches it at its
    next tool boundary (``steer.py``); None when the target is not one, or
    its execution cannot take a steer now, and the message queues instead.

    The inbox entry goes first, marked with the steer's command id, so a
    steer the turn never took is delivered when the turn ends."""
    from openprogram.runtime.conversation.peer_agents import inbox
    from . import steer

    helper = steer.spawned_running_job(run_session, target_tip or None, sid)
    if helper is None:
        return None
    command_id = steer.submit(
        helper.id, delivery_message, sender_session_id=sid, sender_name=from_name,
        authority=caller_authority,
    )
    if command_id is None:
        return None
    try:
        inbox.enqueue(
            run_session, message=message, sender_session_id=sid, sender_msg_id=aid,
            sender_agent_id=parent_agent, agent_id=chosen_agent, chain_messages=messages,
            chain_generations=generations, target_head_id=target_tip,
            authority=caller_authority, scope="session" if run_session != sid else "branch",
            sender_name=from_name, label=label, chain_ancestors=ancestors,
            steer={"command_id": command_id, "execution_id": helper.id}, dedup=False,
        )
    except Exception:  # noqa: BLE001 - the steer itself is stored with the execution
        _log.warning("steer fallback entry not queued for %s", helper.id, exc_info=True)
    return (
        f"[steering] execution_id={helper.id} target={run_session}:{target_tip}\n"
        "It is your helper and still running: your message reaches it at its "
        "next tool step. Its result comes back to you as before. You are not "
        "blocked — continue."
    )


def hold_for_relay(sent) -> None:
    """When this send passes on a message the turn was itself sent (A asked
    B; B, in that turn, asks C), A's answer waits for C's reply to reach B
    and for B's turn on it to end, so A gets B's real answer rather than
    "I asked C" (the hand-off's ``owed_after`` chain). ``sent``: the
    delivery Job, or its id."""
    try:
        from openprogram.runtime.jobs import get_runner, notices
        from openprogram.runtime.jobs.runner import _current_job_id
        current = _current_job_id.get()
        if not current:
            # The conversation's own turn: nobody waits on it.
            return
        runner = get_runner()
        if isinstance(sent, str):
            sent = runner.get_job(sent)
            if sent is None:
                return
        passing_on = runner.get_job(current)
        if (passing_on is None or passing_on.message_origin != "agent_delivery"
                or passing_on.wait or passing_on.id == sent.id):
            return
        notices.hold(passing_on, waits_on=sent)
    except Exception:  # noqa: BLE001 - without a hold A hears B's own turn
        _log.warning("relay hold not recorded for %s", getattr(sent, "id", "?"), exc_info=True)
