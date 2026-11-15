"""The ``chat`` action's last phases: publish the running task, start the turn.

Every failure here unwinds what the earlier phases took in the same order
they always have: the surface bindings, the admission and the run
reservation (plus the running-task entry once it is published).
"""
from __future__ import annotations

import logging
import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .context_usage import _record_canonical_context_stats

_log = logging.getLogger(__name__)

# Turn failures keep logging under the ``chat`` action's own module.
_TURN_LOGGER = f"{__package__}.turn"


@dataclass
class _AdmittedTurn:
    """An admitted turn: what launch and every unwinding path need."""
    session_id: str
    msg_id: str
    conv: dict
    adapter: Any
    admission: Any
    surface_context: Any
    release_surface_bindings: Callable[[Any], None]
    response_format: Any
    execution_id: Any
    # A turn on a recorded branch that is not selected: it holds that
    # branch's slot and reports its state with ``branch_running_task``.
    side_branch_id: Any = None
    branch_from: Any = None
    # The branch the turn was admitted on, selected then or not. It runs and
    # writes there whatever the person selects meanwhile.
    branch_id: Any = None


def _emit_turn_state(_s, turn: "_AdmittedTurn", *, cleared: bool = False,
                     where: Any = None) -> None:
    """Broadcast this turn's run state on the slot it holds now: the
    selected branch's (``running_task``) or its branch's
    (``branch_running_task``), wherever a switch has moved it.

    Its end goes on the slot it was released from (``where``, as
    ``_take_owned_run`` returns it): a switch while it ran already cleared
    it from the place it left. With no slot to go by (released earlier, by
    a stop or a failed start), it is sent as the selected run, and also as
    its branch's run unless that branch is the one selected now, each
    naming the turn, so a client clears it whichever branch it shows."""
    if not cleared:
        _s._emit_slot_state(turn.session_id, turn.msg_id)
        return
    from openprogram.interfaces.server.server_runtime.run_slots import SELECTED
    ids = {"cleared_msg_id": turn.msg_id, "cleared_execution_id": turn.execution_id}
    if where is None:
        branch = turn.side_branch_id or turn.branch_id
        _s._emit_running_task_event(turn.session_id, **ids)
        if branch and _s._selected_branch(turn.session_id) != branch:
            _s._emit_branch_running_task_event(turn.session_id, branch, **ids)
    elif where == SELECTED:
        _s._emit_running_task_event(turn.session_id, **ids)
    else:
        _s._emit_branch_running_task_event(turn.session_id, where, **ids)


def _publish_running_task(_s, turn: _AdmittedTurn) -> None:
    # Mark the session running + push the sidebar list right now, before
    # the exec thread starts — so every connected tab shows the new
    # conversation row already flowing (convRunningFlow) the instant the
    # turn is dispatched, not a round-trip later when the exec thread's
    # own running_task broadcast lands. setdefault so the thread's later
    # _running_tasks[...] = {...} overwrite stays the single source of
    # the task entry (no double running_task with a different started_at).
    import time as _t
    session_id, msg_id = turn.session_id, turn.msg_id
    execution_id = turn.execution_id
    _admission = turn.admission
    if turn.side_branch_id:
        # The branch's slot was reserved by this message; it learns the
        # admitted ids. The conversation's slot and the sidebar are the
        # selected branch's and are left alone.
        _s._set_branch_run_execution(session_id, msg_id, execution_id=execution_id,
                                     status_version=_admission.status_version)
        _emit_turn_state(_s, turn)
        return
    try:
        # The reservation is created before the admission has an execution
        # id. Fill that exact id into the slot this message owns, the
        # selected one or, after a switch, its branch's.
        _s._set_branch_run_execution(session_id, msg_id, execution_id=execution_id,
                                     status_version=_admission.status_version)
        _emit_turn_state(_s, turn)
    except BaseException as exc:
        with _s._running_tasks_lock:
            if (_s._running_tasks.get(session_id) or {}).get("msg_id") == msg_id:
                _s._running_tasks.pop(session_id, None)
        turn.adapter.fail_admission(
            _admission, reason_code="chat_handoff_failed", failure_text=f"{type(exc).__name__}: {exc}",
        )
        turn.release_surface_bindings(turn.surface_context)
        _s._release_run_reservation(session_id, msg_id)
        raise
    # The sidebar list reads every session row; build it off the event loop.
    # A one-row ``session_updated`` cannot replace it yet: peers drop rows
    # they do not know and do not re-sort on activity from that frame.
    from openprogram.interfaces.server.ws_actions.session import broadcast_sessions_list

    def _broadcast_list() -> None:
        try:
            broadcast_sessions_list()
        except Exception:
            # Runs off-loop; sidebars refetch the list on their next load.
            _log.debug("sessions list broadcast failed", exc_info=True)

    try:
        asyncio.get_running_loop().run_in_executor(None, _broadcast_list)
    except RuntimeError:
        _broadcast_list()


def _clear_running_task(_s, turn: _AdmittedTurn) -> None:
    _emit_turn_state(_s, turn, cleared=True)


def _make_run_thread(_s, turn: _AdmittedTurn, **kwargs):
    try:
        return threading.Thread(**kwargs)
    except BaseException as exc:
        turn.release_surface_bindings(turn.surface_context)
        turn.adapter.fail_admission(
            turn.admission, reason_code="agent_runner_error", failure_text=f"{type(exc).__name__}: {exc}",
        )
        _s._release_run_reservation(turn.session_id, turn.msg_id)
        _clear_running_task(_s, turn)
        raise


def _canonical_runner(_s, turn: _AdmittedTurn):
    """The run thread's target: activate the admission and finish the run."""
    session_id, msg_id = turn.session_id, turn.msg_id
    _adapter, _admission = turn.adapter, turn.admission

    def _run_canonical(**_thread_options):
        def _publish_activation(active):
            _s._set_branch_run_execution(session_id, msg_id,
                                         status_version=active.status_version)
            _emit_turn_state(_s, turn)

        async def _activate():
            return await _adapter.activate(_admission, on_activated=_publish_activation)

        try:
            activated = asyncio.run(_activate())
            from openprogram.interfaces.server.server_runtime.run_slots import SELECTED
            if (activated is not None and not turn.side_branch_id
                    and _s._slot_location(session_id, msg_id) == SELECTED):
                # Context stats are the selected branch's, shown on its mirror.
                _active, turn_result = activated
                # Stats are a derived projection; a transient failure here
                # must not change the already-committed turn's outcome.
                try:
                    _record_canonical_context_stats(
                        session_id, msg_id, turn.conv, turn_result,
                    )
                except Exception:
                    import logging
                    logging.getLogger(_TURN_LOGGER).exception(
                        "failed to record context stats for %s", session_id,
                    )
        finally:
            turn.release_surface_bindings(turn.surface_context)
            try:
                # A stop frees the slot while the execution is still ending, and
                # the frame sent then still names it as the running turn. The
                # turn's end is sent again now that it is over, so a stopped
                # turn ends with a clear like any other; a slot another owner
                # has taken since is not announced here.
                where = _s._take_owned_run(session_id, msg_id)
                _emit_turn_state(_s, turn, cleared=True, where=where)
            except Exception:
                # Worker exit path: an exception here would die unobserved.
                _log.warning(
                    "running task not cleared for session %s", session_id, exc_info=True)

    return _run_canonical


def _start_run_thread(_s, turn: _AdmittedTurn) -> None:
    session_id, msg_id = turn.session_id, turn.msg_id
    run_thread = _make_run_thread(
        _s, turn,
        target=_canonical_runner(_s, turn),
        args=(),
        kwargs={"response_format": turn.response_format},
        daemon=True,
    )

    if not _s._activate_run_reservation(session_id, msg_id, run_thread):
        turn.release_surface_bindings(turn.surface_context)
        turn.adapter.fail_admission(
            turn.admission, reason_code="agent_runner_error",
            failure_text="chat execution reservation was lost before startup",
        )
        _s._release_run_reservation(session_id, msg_id)
        _clear_running_task(_s, turn)
        raise RuntimeError("chat execution reservation was lost before startup")

    try:
        run_thread.start()
    except BaseException as exc:
        turn.release_surface_bindings(turn.surface_context)
        turn.adapter.fail_admission(
            turn.admission, reason_code="agent_runner_error", failure_text=f"{type(exc).__name__}: {exc}",
        )
        where = _s._take_owned_run(session_id, msg_id)
        if where is not None:
            _emit_turn_state(_s, turn, cleared=True, where=where)
        _s._release_run_reservation(session_id, msg_id)
        raise
