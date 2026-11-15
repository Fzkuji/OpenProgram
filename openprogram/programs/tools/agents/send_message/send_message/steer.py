"""A message from a helper's own spawner reaches it mid-turn, as a steer.

A message to a running agent normally waits in its inbox until the turn
ends (``inbox.py``). When the sender is the agent that started that helper
(or the person, through Activity's "Add instruction"), it is delivered at
the helper's next tool boundary instead, through the same durable
execution command the person's steer uses (``execution.steer``): the
command is stored with the helper's execution and the turn takes it at its
next safe point (``loop_runner._steering_source``). Anyone else's message
queues. This follows Hermes ``action: steer`` and Grok's steer delivery;
the relation decides, there is no parameter.

The inbox entry is written first and carries the command's id
(``steer``), so a steer the turn never reached (it ended before another
tool boundary) is delivered at turn end like any queued message, and one
it took is dropped (``inbox.drain``).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

_log = logging.getLogger(__name__)

#: The command's actor names the agent that sent it, never a person.
AGENT_SURFACE = "agent"
#: Stored on the steer command and the message it writes.
AGENT_ORIGIN = "agent"


def spawned_running_job(run_session: str, tip: Optional[str], sender_session_id: str):
    """The running helper on ``run_session:tip`` that the sending turn
    started, or None. The sender is its spawner when the helper was created
    by ``agent`` from the sender's conversation, by the same Job the sender
    runs in now (none: the conversation's own turn)."""
    if not tip:
        return None
    try:
        from openprogram.runtime.jobs.runner import _current_job_id
        from openprogram.runtime.jobs.store import list_jobs
        from openprogram.runtime.jobs.types import JobStatus, branch_nodes
    except Exception:  # noqa: BLE001 - no Jobs package: nothing to steer
        return None
    current = _current_job_id.get()
    for job in list_jobs(run_session):
        if job.status not in (JobStatus.RUNNING, JobStatus.QUEUED):
            continue
        if not job.creates_agent or job.wait:
            continue
        if tip not in branch_nodes(job):
            continue
        if (job.caller_session_id or job.parent_session_id) != sender_session_id:
            continue
        if (job.parent_job_id or None) != (current or None):
            continue
        return job
    return None


def submit(execution_id: str, text: str, *, sender_session_id: str,
           sender_name: str, authority: dict[str, Any]) -> Optional[str]:
    """Store ``text`` as a steer on the helper's execution; its command id,
    or None when the execution cannot take one now (not running yet, ended,
    or no steering)."""
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution import hooks
        from openprogram.runtime.execution.model import TERMINAL_EXECUTION_STATUSES
        from openprogram.state.authority import normalize_authority
    except Exception:  # noqa: BLE001
        return None
    store = default_store()
    execution = store.get_execution(execution_id)
    if execution is None or execution.status in TERMINAL_EXECUTION_STATUSES:
        return None
    if not getattr(execution.capabilities, "steer", False):
        return None
    runner = hooks.job_runner(store)
    if runner is None:
        return None
    command_id = f"send_{uuid.uuid4().hex[:16]}"
    actor = {**normalize_authority(authority or {}), "surface": AGENT_SURFACE,
             "sender_session_id": sender_session_id}
    try:
        runner._execution_control.request_steer(  # noqa: SLF001 - the Job runner owns its control
            command_id=command_id, execution_id=execution_id,
            expected_version=execution.status_version, actor=actor,
            payload={"message": text, "origin": AGENT_ORIGIN,
                     "sender_session_id": sender_session_id, "sender_name": sender_name},
        )
    except Exception:  # noqa: BLE001 - queued at turn end instead
        _log.info("steer to %s not accepted; the message queues", execution_id, exc_info=True)
        return None
    return command_id


def taken(command_id: str) -> bool:
    """Whether the helper's turn took the steer (it wrote the message)."""
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.model import CommandStatus
        command = default_store().get_command(command_id)
    except Exception:  # noqa: BLE001 - unknown: deliver it at turn end
        return False
    return command is not None and command.status in (CommandStatus.APPLYING, CommandStatus.APPLIED)


__all__ = ["AGENT_ORIGIN", "spawned_running_job", "submit", "taken"]
