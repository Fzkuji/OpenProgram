"""The Agent engine's registrations with the execution control plane.

Imported by ``openprogram.runtime.conversation`` itself, so any process that
loads conversations can have its Agent executions activated, restarted and
cancelled by ``openprogram.runtime.execution``. Every hook imports the engine
lazily, when the control plane first calls it. Importing this module also
loads ``openprogram.runtime.jobs``, which registers the Job hooks: a
conversation spawns and cancels Jobs.

The conversation engine also answers for the Programs that react when an
execution ends or a worker restarts (a chat Goal runs as conversation
turns): those register with the conversation's turn moments
(``openprogram.runtime.conversation.turn_hooks``), whose lookup loads the
Programs first, so these hooks never depend on when a Program loaded.
"""
from __future__ import annotations

import logging
from typing import Any

from openprogram.runtime.execution import hooks

import openprogram.runtime.jobs  # noqa: F401  (registers the Job hooks)

_log = logging.getLogger(__name__)


def _agent_activator(executions: Any, service: Any):
    # A resumed turn (after an approval, say) streams its output to every
    # client like the turn that started it; without a sink the transcript
    # stayed still until the turn ended.
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.events import emit_ws_frame

    driver = AgentProductionDriver(
        executions, control_service=service, event_sink=emit_ws_frame,
        run_ended=_run_ended,
    )
    return driver.activate


def _run_ended(executions: Any, execution: Any, result: Any) -> None:
    from openprogram.runtime import scheduler_link

    scheduler_link.run_ended(executions, execution, result)


def _start_recovery_turn(store: Any, execution: Any, event: Any) -> str | None:
    from openprogram.runtime.conversation.turn_admission.restart_turn import start_recovery_turn

    return start_recovery_turn(store, execution, event)


async def _reconcile_permission_waits(session_id: str, *, service: Any) -> None:
    from openprogram.runtime.permissions import reconcile_permission_waits

    await reconcile_permission_waits(session_id, service=service)


class _LiveOwnerSignals:
    """Stop and kill the process runner and exec runtime of one execution."""

    def request_graceful_stop(self, session_id: str, execution_id: str) -> None:
        from openprogram.runtime.conversation.process_runner import request_graceful_stop

        request_graceful_stop(session_id, execution_id=execution_id)

    def kill(self, session_id: str, execution_id: str) -> bool:
        killed = False
        try:
            from openprogram.runtime.conversation.process_runner import kill_active_subprocess

            if kill_active_subprocess(session_id, execution_id=execution_id):
                killed = True
        except Exception:
            # Stop path: still try the exec runtime below even if this kill failed.
            _log.warning("kill of subprocess failed for session %s execution %s", session_id, execution_id, exc_info=True)
        try:
            from openprogram.runtime.conversation.run_control.active_runtimes import (
                kill_active_runtime,
            )

            kill_active_runtime(session_id, execution_id=execution_id)
            killed = True
        except Exception:
            # Stop path: report whatever the subprocess kill achieved.
            _log.warning("kill of exec runtime failed for session %s execution %s", session_id, execution_id, exc_info=True)
        return killed


def _settle_terminal(executions: Any, execution: Any) -> None:
    from openprogram.runtime.conversation import turn_hooks

    try:
        # What Programs account for the ended execution (a chat Goal's usage
        # and next turn). A handler defers what it cannot settle now itself.
        for settle in turn_hooks.handlers("execution_terminal"):
            settle(executions, execution)
    finally:
        # However the run ended (finished, failed, stopped by the user, cut off): its own pages go with it.
        from openprogram.runtime import scheduler_link

        session_id = str(getattr(execution, "session_id", "") or "")
        execution_id = str(getattr(execution, "execution_id", "") or "")
        scheduler_link.close_run_pages(execution_id)
        # What Programs kept for the run (the screen tool's code interpreter,
        # an agent's tabs and element ids) ends too.
        for release in turn_hooks.handlers("execution_released"):
            try:
                release(session_id, execution_id)
            except Exception:
                _log.warning("what %s kept for execution %s was not released",
                             getattr(release, "__qualname__", release), execution_id, exc_info=True)


def _close_ownerless_run(executions: Any, execution: Any) -> None:
    from openprogram.runtime import scheduler_link
    from openprogram.runtime.execution.model import ExecutionStatus

    if execution.status is not ExecutionStatus.CANCELLED:
        scheduler_link.run_ended(executions, execution, None)


class _SessionGoals:
    """``execution.hooks.SessionGoals`` over the turn moments a chat Goal registers.

    Without one, nothing owns a session and nothing pauses after a restart.
    """

    def owner(self, store: Any, session_id: str):
        from contextlib import nullcontext

        from openprogram.runtime.conversation import turn_hooks

        owner = turn_hooks.provider("session_owner")
        return nullcontext(True) if owner is None else owner(store, session_id)

    def pause_for_restart(self, store: Any, session_id: str) -> bool:
        from openprogram.runtime.conversation import turn_hooks

        pause = turn_hooks.provider("restart_pause")
        return False if pause is None else pause(store, session_id)


hooks.register("agent_activator", _agent_activator)
hooks.register("recovery_turn_starter", _start_recovery_turn)
hooks.register("permission_wait_reconciler", _reconcile_permission_waits)
hooks.register("live_owner_signals", _LiveOwnerSignals())
hooks.register("terminal_settler", _settle_terminal)
hooks.register("ownerless_run_closer", _close_ownerless_run)
hooks.register("session_goals", _SessionGoals())
