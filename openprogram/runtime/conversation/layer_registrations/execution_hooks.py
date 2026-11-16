"""The Agent engine's registrations with the execution control plane.

Imported by ``openprogram.runtime.conversation`` itself, so any process that
loads conversations can have its Agent executions activated, restarted and
cancelled by ``openprogram.runtime.execution``. Every hook imports the engine
lazily, when the control plane first calls it. Importing this module also
loads ``openprogram.runtime.jobs``, which registers the Job hooks: a
conversation spawns and cancels Jobs.

The conversation engine also registers the Goal hooks. A chat Goal runs as
conversation turns, and the Goal tool (``openprogram.programs.tools.planning
.goal``) loads on first use, which can be later than startup recovery; these
hooks import it when first called, so registration never depends on when the
tool loaded.
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
    from openprogram.runtime.scheduler import run_finish

    run_finish.finish_resumed(executions, execution, result)


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
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat

    try:
        chat.after_terminal(executions, execution)
    except (goals.GoalConflictError, goals.GoalStateUnavailable):
        # The Goal's lock stayed held or its usage is not in yet. The turn is
        # over either way, and whoever closes it (a Stop's cancelled reply,
        # resource release) goes on. The accounting is not lost: the turn's
        # owner retries it (``chat.settle_terminal``), and a Goal left on an
        # ended turn is settled when it is next read (``chat.recover_unsettled``).
        _log.info("Goal accounting of %s deferred", getattr(execution, "execution_id", ""), exc_info=True)
    finally:
        # However the run ended (finished, failed, stopped by the user, cut off): its own pages go with it.
        from openprogram.runtime.scheduler import run_finish

        run_finish.close_run_tabs(str(getattr(execution, "execution_id", "") or ""))


def _close_ownerless_run(executions: Any, execution: Any) -> None:
    from openprogram.runtime.execution.model import ExecutionStatus
    from openprogram.runtime.scheduler import run_finish

    if execution.status is not ExecutionStatus.CANCELLED:
        run_finish.finish_resumed(executions, execution, None)


class _SessionGoals:
    """``execution.hooks.SessionGoals`` over the Goal tool."""

    def owner(self, store: Any, session_id: str):
        from openprogram.programs.tools.planning.goal.ownership import goal_owner

        return goal_owner(store, session_id)

    def pause_for_restart(self, store: Any, session_id: str) -> bool:
        from openprogram.programs.tools.planning.goal.restart import pause_for_worker_restart

        return pause_for_worker_restart(store, session_id)


hooks.register("agent_activator", _agent_activator)
hooks.register("recovery_turn_starter", _start_recovery_turn)
hooks.register("permission_wait_reconciler", _reconcile_permission_waits)
hooks.register("live_owner_signals", _LiveOwnerSignals())
hooks.register("terminal_settler", _settle_terminal)
hooks.register("ownerless_run_closer", _close_ownerless_run)
hooks.register("session_goals", _SessionGoals())
