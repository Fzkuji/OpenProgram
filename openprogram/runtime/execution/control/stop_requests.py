"""A stop accepted in one process reaches the process that runs the turn.

The control service hands a command to the live driver binding, and that
binding exists only in the process running the attempt. A scheduled run runs
its turn in a process of its own, so a stop the Worker accepts finds no owner
there. The Worker then writes the request into the execution's function log,
the per-execution file every process of the profile can read
(:mod:`openprogram.runtime.execution.function_log`), and the run's process,
which follows that file while its turn runs, submits the stop to its own
control service. From there it is an ordinary local stop: the turn is
interrupted, a running tool included, and ends cancelled through its usual
ending.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

_log = logging.getLogger(__name__)

#: Seconds between looks at the file while a turn runs.
FOLLOW_POLL_S = 0.5
CANCEL = "cancel"


def forward(executions: Any, execution: Any, command: str) -> bool:
    """Ask the other live process holding ``execution`` to act on ``command``.

    Returns whether the request was written; a turn of this process, or of a
    process that is gone, gets none."""
    from ..process_owner import owned_by_another_process, process_owner_may_be_alive

    lease = execution.owner_lease
    generation = lease.get("generation")
    if (not isinstance(generation, int) or not owned_by_another_process(lease)
            or not process_owner_may_be_alive(lease)):
        return False
    log = executions.function_log(execution.execution_id)
    if log is None:
        return False
    log.request(command, generation)
    return True


def _stop_if_asked(execution_id: str, handled: set[int]) -> None:
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.model import ExecutionStatus, TERMINAL_EXECUTION_STATUSES
    from openprogram.runtime.execution.process_owner import owned_by_another_process
    from openprogram.runtime.execution.run_control import ExecutionNotCancellable, cancel_execution

    store = default_store()
    execution = store.get_execution(execution_id)
    if execution is None or execution.status in TERMINAL_EXECUTION_STATUSES:
        return
    generation = execution.owner_lease.get("generation")
    if (not isinstance(generation, int) or generation in handled
            or owned_by_another_process(execution.owner_lease)):
        return
    log = store.function_log(execution_id)
    if log is None or CANCEL not in log.requested(generation):
        return
    handled.add(generation)
    if execution.status is ExecutionStatus.CANCELLING:
        return  # Stopped here already; the request is the same one.
    try:
        cancel_execution(execution_id)
    except ExecutionNotCancellable:
        return


def follow(execution_id: str, *, poll_s: float = FOLLOW_POLL_S) -> Callable[[], None]:
    """While this process runs ``execution_id``, act on a stop asked for elsewhere.

    Returns the call that stops following."""
    done = threading.Event()
    handled: set[int] = set()

    def run() -> None:
        while not done.wait(poll_s):
            try:
                _stop_if_asked(execution_id, handled)
            except Exception:  # noqa: BLE001 - the next look tries again
                _log.warning("stop request of %s not read", execution_id, exc_info=True)

    thread = threading.Thread(target=run, name=f"stop-requests-{execution_id}", daemon=True)
    thread.start()

    def stop() -> None:
        done.set()
        thread.join(poll_s + 1.0)

    return stop
