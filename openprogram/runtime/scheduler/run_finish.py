"""How a run of a scheduled task comes to its end.

A run stops in one of three places: in its own process, in a new Worker that
picked up the turn after a restart, or in its own process after the Worker that
started it has gone. All three go through :func:`conclude` and :func:`finish`,
so they get the same record and the same rule for telling the user.

Only a terminal execution is an ending. A pause, an effect still being settled,
or a turn saved during a version switch goes on later, and only where it truly
ends does anything get recorded or sent. A run the user stopped records nothing:
they know, and it says nothing about whether the task works. A run stopped at
its time limit is a failure, since nobody asked for that. An empty reply counts
as nothing new.

The process that runs a turn keeps its ending on disk before telling the Worker
(:func:`keep`), and the Worker's scheduler picks up what no Worker heard
(:func:`finish_kept`). ``task_runs`` counts a turn once, by its execution id, so
one that arrives by two of these paths still tells the user once.

The user is told before the row is written, because the row is what marks a run
as done. A report that raises leaves the ending on disk with its failure count
and an attempt number; each tick tries again, up to ``ANNOUNCE_ATTEMPTS``, and
after the last only the row is saved.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from collections.abc import Mapping
from typing import Any

from openprogram.runtime.execution.model import TERMINAL_EXECUTION_STATUSES, ExecutionStatus

from . import task_branch, task_runs, time_limits
from openprogram.host._platform.filesystem import replace_file

_LOG = logging.getLogger(__name__)

_ENDED_BROKEN = frozenset({ExecutionStatus.FAILED, ExecutionStatus.INTERRUPTED})
ANNOUNCE_ATTEMPTS = 5
# The run's own thread and the tick can both hold one ending; one at a time, so
# the second finds the row and says nothing.
_finishing = threading.Lock()


def held_for_switch(result: Any) -> bool:
    """A turn saved during a version switch: the run happens after the switch."""
    from openprogram.operations.self_update.control.held_turns import HELD_REASON

    return getattr(result, "error_reason", None) == HELD_REASON


def has_ended(result: Any, execution: Any) -> bool:
    """Whether the run is over, rather than stopped at a point it goes on from."""
    if getattr(result, "_execution_safe_point_handoff", False) or held_for_switch(result):
        return False
    return execution is None or execution.status in TERMINAL_EXECUTION_STATUSES


def conclude(session_id: str, task: dict[str, Any], execution_id: str, result: Any,
             execution: Any) -> dict[str, Any] | None:
    """Settle the task's branch on the run's tip and say what the run has for
    the user; ``None`` while the run has not ended, or when the user stopped it."""
    if not has_ended(result, execution):
        return None
    tip = getattr(result, "assistant_msg_id", None)
    task_branch.finish(session_id, task, None, tip)
    ended_as = getattr(execution, "status", None)
    if ended_as is ExecutionStatus.CANCELLED and not time_limits.stopped_by_limit(execution):
        return None
    if time_limits.stopped_by_limit(execution):
        status, text = task_runs.FAILED, time_limits.SCHEDULED_RUN_STOPPED
    elif result is None or getattr(result, "failed", False) or ended_as in _ENDED_BROKEN:
        status, text = task_runs.FAILED, str(getattr(result, "error", None) or (
            "the run was interrupted and could not continue" if ended_as is ExecutionStatus.INTERRUPTED
            else "the run ended without a reply"))
    else:
        reply = str(getattr(result, "final_text", None) or "")
        last_round = str(getattr(result, "last_round_text", None) or "")
        judged = task_branch.judged_text(session_id, reply, last_round)
        reason = task_branch.unable_reason(judged)
        if reason is not None:
            status, text = task_runs.FAILED, reason or "the check could not be done"
        elif not task_branch.is_quiet(judged):
            status, text = task_runs.RESULT, reply
        elif getattr(result, "attached_files", None) and last_round.strip():
            # A run that sent a file has said something, but the mark is not for the user.
            before, _mark, after = reply.rpartition(last_round.strip())
            status, text = task_runs.RESULT, (before + after).strip()
        else:
            status, text = task_runs.QUIET, ""
    return {"session_id": session_id, "task_id": task["id"], "title": task.get("title") or "",
            "status": status, "text": text, "tip": tip, "execution_id": execution_id,
            "attached_files": list(getattr(result, "attached_files", None) or [])}


def finish(outcome: Mapping[str, Any]) -> None:
    """In the Worker, where clients listen: tell the user when the run has
    something to say or the task keeps failing, then record the run."""
    with _finishing:
        _finish(dict(outcome))


def close_run_tabs(execution_id: str, *, wait: bool = False) -> None:
    """A run starts clean and ends with its answer: the pages it opened close with it.

    Safe for any execution that ended: only a scheduled run's and a check-in's
    pages carry the run they were opened in. The Worker does not wait for the
    App; a run's own process passes ``wait`` so the pages are closed before
    the process exits."""
    try:
        from openprogram.programs._host import services

        browser = services.lookup("browser")
        if browser is not None and execution_id:
            if wait:
                browser.tabs.close_run_tabs(execution_id)
            else:
                browser.tabs.close_run_tabs_soon(execution_id)
    except Exception:  # noqa: BLE001 - a page left open is bounded by the conversation's limit
        _LOG.warning("pages of run %s not closed", execution_id, exc_info=True)


def _finish(outcome: dict[str, Any]) -> None:
    session_id = str(outcome.get("session_id") or "")
    execution_id = str(outcome.get("execution_id") or "")
    # Every ending the Worker hears comes through here, the ones kept on disk too.
    close_run_tabs(execution_id)
    try:
        from openprogram.runtime import interface_link

        if session_id:
            interface_link.branches_changed(session_id)
        task_id = str(outcome.get("task_id") or "")
        status, text = str(outcome.get("status") or task_runs.QUIET), str(outcome.get("text") or "")
        if not _still_scheduled(task_id):
            _forget(execution_id)
            return
        failures = task_runs.failures_after(task_id, status, execution_id=execution_id or None)
        if failures is None:
            _forget(execution_id)
            return
        outcome.setdefault("failures", failures)
        if not outcome.get("told"):
            _tell(outcome, status, text, int(outcome["failures"]))
            outcome["told"] = True
        _record(outcome, task_id, status, text, execution_id)
        _forget(execution_id)
        # The report's own frame went out before the row existed.
        if session_id:
            interface_link.broadcast({"type": "always_on_update", "data": {"session_id": session_id}})
    except Exception:  # noqa: BLE001 - the run itself is kept on its branch
        _LOG.warning("scheduled run in %s not announced", session_id, exc_info=True)
        _retry_later(outcome, execution_id)


def _tell(outcome: Mapping[str, Any], status: str, text: str, failures: int) -> None:
    from openprogram.runtime.conversation.always_on import runtime as always_on

    session_id, task_id = str(outcome.get("session_id") or ""), str(outcome.get("task_id") or "")
    title = str(outcome.get("title") or "Scheduled task")
    if status == task_runs.RESULT:
        always_on.report_scheduled(session_id, task_id, title, text,
                                   attached_files=outcome.get("attached_files") or ())
    elif status == task_runs.FAILED and failures == task_runs.FAILURE_NOTICE:
        always_on.report_scheduled(session_id, task_id, title, task_runs.failure_message(title, failures, text))


def _record(outcome: Mapping[str, Any], task_id: str, status: str, text: str, execution_id: str) -> None:
    task_runs.record(task_id, status, text, outcome.get("tip"), execution_id=execution_id or None,
                     failed_check=bool(outcome.get("failed_check")))


def _retry_later(outcome: dict[str, Any], execution_id: str) -> None:
    """Keep the ending for the next tick; past the last attempt only the row is left to save."""
    attempts = int(outcome.get("attempts") or 0) + 1
    if attempts < ANNOUNCE_ATTEMPTS:
        # A firing no turn ran has no execution id, and an ending is kept under one.
        outcome.update(attempts=attempts, execution_id=execution_id or f"firing-{uuid.uuid4().hex}")
        keep(outcome)
        return
    _LOG.warning("gave up announcing scheduled run %s after %d attempts", execution_id, attempts)
    try:
        _record(outcome, str(outcome.get("task_id") or ""), str(outcome.get("status") or task_runs.QUIET),
                str(outcome.get("text") or ""), execution_id)
    except Exception:  # noqa: BLE001 - nothing further can be done for this run
        _LOG.warning("scheduled run %s not recorded", execution_id, exc_info=True)
    _forget(execution_id)


def _still_scheduled(task_id: str) -> bool:
    """A task the user deleted has no page to record on and nothing to say."""
    from openprogram.programs.tools.jobs.cron.cron import _load, _resolve_path

    return any(entry.get("id") == task_id for entry in _load(_resolve_path()))


def finish_held(request: Any, result: Any) -> None:
    """A turn saved during a version switch has run after it, outside any execution."""
    task = getattr(request, "scheduled_task", None)
    if not isinstance(task, Mapping) or not task.get("id"):
        return
    outcome = conclude(request.session_id, dict(task), f"held-{request.user_msg_id}", result, None)
    if outcome is not None:
        finish(outcome)


def scheduled_task_of(executions: Any, execution: Any) -> dict[str, Any] | None:
    """The task this execution is a run of, if it is one."""
    payload = executions.get_agent_turn_input(execution.execution_id) or {}
    task = (payload.get("request") or {}).get("scheduled_task")
    return dict(task) if isinstance(task, Mapping) and task.get("id") else None


def finish_resumed(executions: Any, execution: Any, result: Any) -> None:
    """A turn this Worker continued or closed has stopped: finish it if it is a
    task's run. ``result`` is ``None`` when it stopped with no turn running."""
    task = scheduled_task_of(executions, execution)
    if task is None:
        return
    outcome = conclude(execution.session_id, task, execution.execution_id, result, execution)
    if outcome is not None:
        finish(outcome)


def _kept_directory() -> str:
    from openprogram.programs.tools.jobs.cron.cron import _resolve_path

    return os.path.join(os.path.dirname(_resolve_path()) or ".", "run-endings")


def _kept_path(execution_id: str) -> str:
    return os.path.join(_kept_directory(), f"{execution_id}.json")


def _forget(execution_id: str) -> None:
    if not execution_id:
        return
    try:
        os.unlink(_kept_path(execution_id))
    except OSError:
        pass


def keep(outcome: Mapping[str, Any]) -> None:
    """Put a run's ending where the next Worker finds it if this one has gone."""
    try:
        directory = _kept_directory()
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="ending-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(dict(outcome), handle, ensure_ascii=False)
            replace_file(temporary, _kept_path(str(outcome["execution_id"])))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except (OSError, KeyError, TypeError):
        _LOG.warning("ending of scheduled run not kept", exc_info=True)


def finish_kept() -> None:
    """Finish the runs that ended with no Worker to hear about it."""
    try:
        names = [name for name in os.listdir(_kept_directory()) if name.endswith(".json")]
    except OSError:
        return
    for name in names:
        path = os.path.join(_kept_directory(), name)
        with _finishing:
            try:
                with open(path, encoding="utf-8") as handle:
                    outcome = json.load(handle)
            except (OSError, ValueError):
                continue
            if isinstance(outcome, dict):
                _finish(outcome)
