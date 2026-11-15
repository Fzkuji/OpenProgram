"""Hand-offs between a parent and the sub-agent it asked for, kept across a crash.

A sub-agent request crosses three hand-offs, and each one writes what it is
about to do before doing it, so the next Worker can finish it:

  * admission → inbox: an ``agent(to=…)`` Job for a busy target carries its
    inbox entry (``Job.deferred_inbox``) from admission on; the enqueue is
    idempotent by Job id, so recovery recreates a lost entry exactly once.
  * admission → dispatch: a spawn whose attach card is written after
    admission stays held until the card exists. The Job records which
    process submitted it (``Job.submitter``); once that process is gone,
    recovery writes the card if it is missing and publishes the Job.
  * terminal → parent: the terminal write marks the Job ``notice_pending``
    with the writing process; queuing the notice settles it. A mark whose
    writer is gone is replayed. A waited-on Job whose waiter died is told
    to its parent the way a background one is.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

_log = logging.getLogger(__name__)

# Jobs this process admitted and is holding until its accepted side effect
# (the attach card) is written. Recovery leaves these alone.
_accepting: set[str] = set()
_accepting_lock = threading.Lock()


def this_process() -> dict[str, Any]:
    """This process, named so another process can tell whether it still runs."""
    from openprogram.runtime.execution.process_owner import identified_process

    return identified_process()


def process_alive(owner: Any) -> bool:
    """Whether the process ``owner`` names may still be running."""
    if not isinstance(owner, dict):
        return False
    from openprogram.runtime.execution.process_owner import _this_instance

    if owner.get("instance") == _this_instance():
        return True
    pid, started = owner.get("pid"), owner.get("started")
    if not isinstance(pid, int) or pid == os.getpid():
        return False
    from openprogram.host._platform.processes import process_alive as _alive, process_start_token

    if started:
        return process_start_token(pid) == started
    return _alive(pid)


def begin_accept(job_id: str) -> None:
    with _accepting_lock:
        _accepting.add(job_id)


def end_accept(job_id: str) -> None:
    with _accepting_lock:
        _accepting.discard(job_id)


def accept_in_progress(job: Any) -> bool:
    """A held Job whose submitter may still write its card and publish it."""
    from openprogram.runtime.execution.process_owner import _this_instance

    owner = getattr(job, "submitter", None)
    if not isinstance(owner, dict):
        return False
    if owner.get("instance") == _this_instance():
        with _accepting_lock:
            return job.id in _accepting
    return process_alive(owner)


def waiter_gone(job: Any) -> bool:
    """A waited-on Job whose waiting caller no longer runs.

    Only a Job that names its submitter can say; an older record is taken
    to have been answered, as it was before.
    """
    owner = getattr(job, "submitter", None)
    return bool(getattr(job, "wait", False)) and isinstance(owner, dict) and not process_alive(owner)


def ensure_attach_placeholder(job: Any) -> bool:
    """Write the running attach card a held spawn was admitted for, once."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.sub_agent_run import (
        _attach_node,
        write_attach_placeholder_for_spawn,
    )

    card_session = job.caller_session_id or job.parent_session_id
    if _attach_node(default_db(), card_session, job.attach_pointer_id) is not None:
        return True
    written: Optional[str] = write_attach_placeholder_for_spawn(
        session_id=card_session,
        caller_msg_id=job.caller_msg_id,
        label=job.label,
        prompt=job.prompt,
        chosen_agent=job.agent_id,
        node_id=job.attach_pointer_id,
        job_id=job.id,
        target_session_id=job.parent_session_id,
        # The interrupted call this card answers, so the rebuilt card pairs
        # with it as the card written at admission did.
        tool_call_id=job.tool_call_id,
        delivery="tool_result" if job.wait else None,
    )
    return written == job.attach_pointer_id


__all__ = [
    "accept_in_progress",
    "begin_accept",
    "end_accept",
    "ensure_attach_placeholder",
    "process_alive",
    "this_process",
    "waiter_gone",
]
