"""How long an unattended run may go on before it is stopped.

Nobody watches a check-in or a scheduled run, so a stuck one would otherwise
hold its place and keep spending for ever. The clock starts when the run is
admitted and is not reset by a restart, so a resumed run counts its earlier
time too. A stop goes through the execution control path with the reason
:data:`TIME_LIMIT`, and that reason is how each ending says why it stopped.

The Worker checks every run it can reach (:func:`stop_overdue`). A scheduled
run in its own process cannot be signalled from the Worker, so that process
keeps its own clock (:func:`watch`).

A stop is a request, and a turn stuck in a blocking call never acts on it. A
run still stopping :data:`STOP_GRACE_S` later is ended without it, and its
process killed if it has one of its own, so nothing goes on waiting for it.

A run whose own process has exited without ending it (killed, crashed) is
ended the same way as soon as the Worker sees the process gone
(:func:`end_runs_of_exited_processes`): at once when the Worker started that
process, else at the next tick. It ends interrupted, which counts as a failed
run, its pages close and the commands it was waiting on stop; it is not left
for a restart to continue.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from openprogram.host._platform.processes import kill_process_tree
from openprogram.runtime.execution.model import CommandKind, TERMINAL_EXECUTION_STATUSES, ExecutionStatus

_LOG = logging.getLogger(__name__)

TIME_LIMIT = "time_limit"
CHECK_IN_LIMIT_S = 15 * 60.0
SCHEDULED_RUN_LIMIT_S = 2 * 3600.0
COMMAND_LIMIT_S = 2 * 3600.0
# Longer than the control path's own wait before it terminates an attempt.
STOP_GRACE_S = 60.0

SCHEDULED_RUN_STOPPED = "Stopped at the 2 hour limit for a scheduled run"
COMMAND_STOPPED = "Stopped at the 2 hour limit for a scheduled command"

_LIMITS = {"runtime/always_on": CHECK_IN_LIMIT_S, "runtime/scheduler": SCHEDULED_RUN_LIMIT_S}


def stopped_by_limit(execution: Any) -> bool:
    """Whether this execution ended because it ran out of time."""
    return (getattr(execution, "status", None) is ExecutionStatus.CANCELLED
            and getattr(execution, "reason_code", None) == TIME_LIMIT)


def _limit(store: Any, execution: Any) -> float | None:
    """The run's limit; ``None`` for anything a person is attending to."""
    payload = store.get_agent_turn_input(execution.execution_id) or {}
    return _LIMITS.get((payload.get("request") or {}).get("speaker_id"))


def _root(store: Any, execution: Any) -> Any:
    """The run this execution works for: a sub-agent shares its caller's clock.

    A Retry or Create branch starts from a saved point of its source and is a
    run of its own, with its own clock.
    """
    seen = {execution.execution_id}
    while (execution.parent_execution_id and not execution.source_checkpoint_id
           and execution.parent_execution_id not in seen):
        parent = store.get_execution(execution.parent_execution_id)
        if parent is None:
            break
        seen.add(parent.execution_id)
        execution = parent
    return execution


def _stop(store: Any, execution: Any) -> None:
    from openprogram.runtime.execution.run_control import ExecutionNotCancellable, cancel_execution_for_reason
    from . import run_finish

    try:
        result = cancel_execution_for_reason(execution.execution_id, TIME_LIMIT)
    except ExecutionNotCancellable:
        return
    if isinstance(result, dict) and result.get("issue_code") == "already_terminal":
        return  # Ended before this stop; whatever ended it reported it.
    if execution.current_attempt_id is None:
        # Paused with no turn running: nothing else will report this ending.
        current = store.get_execution(execution.execution_id)
        if current is not None and current.status in TERMINAL_EXECUTION_STATUSES:
            run_finish.finish_resumed(store, current, None)


def _ignored_its_stop(store: Any, execution: Any, now: float) -> bool:
    if execution.status is not ExecutionStatus.CANCELLING or execution.reason_code != TIME_LIMIT:
        return False
    stops = store.list_commands(execution.execution_id, kinds=(CommandKind.CANCEL,))
    return bool(stops) and now - stops[0].submitted_at >= STOP_GRACE_S


def _stop_its_commands(session_id: str, execution_id: str) -> None:
    """Stop the commands a run that will not act again was still waiting on.

    A command runs under a supervisor of its own, so it outlives the process
    that started it. Ended this way, a run stops them as its turn would when
    stopped; a command it had already left running in the background goes on."""
    from openprogram.host.processes import ProcessStore, control
    from openprogram.host.processes.store import LIVE

    try:
        processes = ProcessStore()
        records = processes.list(session_id, (execution_id,))
    except Exception:  # noqa: BLE001 - the run's ending does not depend on it
        _LOG.warning("commands of run %s not read", execution_id, exc_info=True)
        return
    for record in records:
        if record.get("execution_id") != execution_id or record["status"] not in LIVE or record.get("background"):
            continue
        try:
            processes.update(record["id"], reported=True)
            control(processes, record["id"], "stop", wait=False)
        except Exception:  # noqa: BLE001 - one command must not shield the others
            _LOG.warning("command %s of run %s not stopped", record["id"], execution_id, exc_info=True)


def _end_without_it(store: Any, execution: Any) -> None:
    """End a run that will not stop by itself. If its thread ever returns, the
    finished execution turns that return away."""
    from openprogram.runtime.conversation.run_control import end_turn
    from openprogram.runtime.execution import default_control_service
    from openprogram.runtime.execution.process_owner import owned_by_another_process, process_owner_may_be_alive
    from openprogram.runtime.execution.run_control import current_token
    from . import run_finish

    execution_id, lease = execution.execution_id, execution.owner_lease
    if owned_by_another_process(lease) and process_owner_may_be_alive(lease):
        kill_process_tree(lease["process_owner"]["pid"])
    default_control_service().recover_owner_loss(
        execution_id, attempt_id=execution.current_attempt_id, generation=lease.get("generation"))
    _stop_its_commands(execution.session_id, execution_id)
    # The stuck thread would otherwise count as a turn in progress for good.
    token = current_token(execution.session_id, execution_id=execution_id)
    if token is not None:
        end_turn(execution.session_id, token)
    run_finish.finish_resumed(store, store.get_execution(execution_id), None)


def _process_exited(store: Any, execution: Any) -> bool:
    """A run of this kind whose own process is gone while it still holds the turn."""
    from openprogram.runtime.execution.process_owner import owned_by_another_process, process_owner_may_be_alive

    lease = execution.owner_lease
    return (execution.current_attempt_id is not None and owned_by_another_process(lease)
            and not process_owner_may_be_alive(lease) and _limit(store, _root(store, execution)) is not None)


def _end_exited(store: Any, execution: Any) -> bool:
    """End a run its process left unfinished; False when it had ended after all."""
    from openprogram.runtime.execution import default_control_service
    from . import run_finish

    # What the process wrote last may be its ending.
    store.tables.take_up_other_process_turns(execution.session_id)
    current = store.get_execution(execution.execution_id)
    if current is None or current.status in TERMINAL_EXECUTION_STATUSES or not _process_exited(store, current):
        return False
    recovered = default_control_service().recover_owner_loss(
        current.execution_id, attempt_id=current.current_attempt_id,
        generation=current.owner_lease.get("generation"), continuable=False).execution
    if recovered.status in TERMINAL_EXECUTION_STATUSES:
        _stop_its_commands(recovered.session_id, recovered.execution_id)
        # A scheduled run's recorded ending closes its pages; a user's stop records
        # nothing, so its pages, and any other run's, close here.
        recorded = run_finish.scheduled_task_of(store, recovered) is not None and not (
            recovered.status is ExecutionStatus.CANCELLED and not stopped_by_limit(recovered))
        if not recorded:
            run_finish.close_run_tabs(recovered.execution_id)
        run_finish.finish_resumed(store, recovered, None)
    return True


def end_runs_of_exited_processes(session_id: str | None = None) -> list[str]:
    """End every run, of ``session_id`` or of all, whose own process exited without ending it.

    Only the Worker does this, and not while it is stopping: a run cut off by
    a Worker restart is the next Worker's to continue."""
    from openprogram.runtime.conversation.run_control import is_worker_stopping
    from openprogram.runtime.execution import default_store
    from openprogram.state.store.session.session_log import is_main_writer

    if not is_main_writer() or is_worker_stopping():
        return []
    store = default_store()
    if session_id:
        store.tables.ensure_session(session_id)
        # Loaded before the run started, the session does not show its turn yet.
        store.tables.take_up_other_process_turns(session_id)
    ended: list[str] = []
    for execution in store.list_nonterminal(session_id=session_id):
        try:
            if _process_exited(store, execution) and _end_exited(store, execution):
                ended.append(execution.execution_id)
        except Exception:  # noqa: BLE001 - one run must not shield the others
            _LOG.warning("run %s of an exited process not ended", execution.execution_id, exc_info=True)
    return ended


def stop_overdue(now: float | None = None) -> list[str]:
    """Stop every run past its limit that this process can reach, and end the
    runs whose own process exited without ending them."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.process_owner import owned_by_another_process, process_owner_may_be_alive

    now = time.time() if now is None else now
    store = default_store()
    stopped: list[str] = end_runs_of_exited_processes()
    for execution in store.list_nonterminal():
        try:
            root = _root(store, execution)
            limit = _limit(store, root)
            if limit is None or now - root.created_at < limit:
                continue
            if root.execution_id == execution.execution_id and _ignored_its_stop(store, execution, now):
                _end_without_it(store, execution)
                stopped.append(execution.execution_id)
                continue
            if owned_by_another_process(execution.owner_lease) and process_owner_may_be_alive(execution.owner_lease):
                continue  # Its own process keeps its clock.
            _stop(store, execution)
            stopped.append(execution.execution_id)
        except Exception:  # noqa: BLE001 - one run must not shield the others
            _LOG.warning("run %s not stopped at its limit", execution.execution_id, exc_info=True)
    return stopped


def watch(execution_id: str) -> Callable[[], None]:
    """Stop this process's run at its limit; returns the call that ends the watch."""
    from openprogram.runtime.execution import default_store

    store = default_store()
    execution = store.get_execution(execution_id)
    limit = _limit(store, execution) if execution is not None else None
    if limit is None:
        return lambda: None

    def expire() -> None:
        current = store.get_execution(execution_id)
        if current is None or current.status in TERMINAL_EXECUTION_STATUSES:
            return
        try:
            _stop(store, current)
        except Exception:  # noqa: BLE001 - runs on its own timer thread
            _LOG.warning("run %s not stopped at its limit", execution_id, exc_info=True)

    timer = threading.Timer(max(0.0, limit - (time.time() - execution.created_at)), expire)
    timer.daemon = True
    timer.start()
    return timer.cancel
