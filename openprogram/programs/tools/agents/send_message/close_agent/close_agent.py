"""close_agent — stop an agent you started and retire it.

One verb for what ``archive_agent`` and a cancel did separately (Codex
``close_agent``, Claude Code ``TaskStop``): a running agent is stopped,
and its branch is archived, so it leaves list_agents and takes no more
messages. Its history stays readable with read_conversation.

``target`` is the ``execution_id`` the agent tool or send_message gave
back, or an address list_agents shows. Stopping is limited to work this
conversation started (the ownership check ``job_output`` used); archiving
an address works as ``archive_agent`` did.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""
from __future__ import annotations

import logging

from openprogram.programs._host.runtime import function

_log = logging.getLogger(__name__)

_DESCRIPTION = (
    "Stop an agent you started and retire it. `target` is the execution_id "
    "the agent tool or send_message returned, or an address from list_agents. "
    "A running agent is stopped (what it wrote so far stays on its branch); "
    "then it leaves list_agents and takes no more messages. Its history stays "
    "readable with read_conversation. Use it when a helper is no longer "
    "needed, is stalled, or went the wrong way."
)


def _close_agent_impl(target: str, reason: str = "") -> str:
    from openprogram.programs.tools.agents.send_message.archive_agent.archive_agent import (
        _archive_agent_impl,
    )
    ident = (target or "").strip()
    if not ident:
        return ("[close_agent error] `target` is required — an execution_id or an "
                "address from list_agents.")
    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.jobs.types import is_terminal
    runner = get_runner()
    job = runner.get_job(ident) if ":" not in ident else None
    if job is None:
        out = _archive_agent_impl(ident, reason)
        return out.replace("[archive_agent", "[close_agent", 1)
    from openprogram.programs.tools.agents.agent._ownership import check_job_ownership
    denied = check_job_ownership(job.id, "close_agent")
    if denied:
        return denied
    stopped = ""
    if not is_terminal(job.status):
        try:
            # cancel.parent: the caller closed it, so no "stopped by the
            # user" notice comes back to the caller about its own act.
            runner._cancel_cascade(  # noqa: SLF001 — the runner's own cascade
                job.id, reason=(reason or "closed by the agent that started it"),
                root_reason_code="cancel.parent",
            )
            stopped = "stopped"
        except Exception as exc:  # noqa: BLE001
            return f"[close_agent error] {type(exc).__name__}: {exc}"
        job = runner.get_job(job.id) or job
    archived = ""
    # A job stopped mid-turn has no head_id yet (it is filled when the turn
    # lands), but its branch already ends at the reply admission named for
    # it: archive that tip, or the stopped agent stays in list_agents.
    from openprogram.runtime.jobs.runner.shared import _turn_head_id
    tip = job.head_id or _turn_head_id(getattr(runner, "_execution_store", None), job.id, None)
    if tip:
        out = _archive_agent_impl(f"{job.parent_session_id}:{tip}", reason)
        if not out.startswith("[archive_agent error]"):
            archived = "archived"
    try:
        from openprogram.runtime.jobs import notices
        notices.consume(job)
    except Exception:
        _log.debug("notice of closed job %s not dropped", job.id, exc_info=True)
    done = " and ".join(part for part in (stopped, archived) if part) or "already finished"
    return (f"[close_agent] {job.label or job.id}: {done}. Its history stays readable "
            f"with read_conversation(\"{job.id}\").")


@function(name="close_agent", description=_DESCRIPTION, toolset=["core"])
def close_agent(target: str, reason: str = "") -> str:
    """Stop and retire an agent this conversation started.

    Args:
        target: The execution_id from the agent tool or send_message, or an
            address from list_agents.
        reason: Optional note kept with the archived agent.
    """
    return _close_agent_impl(target=target, reason=reason)
