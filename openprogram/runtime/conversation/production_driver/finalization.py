"""AgentProductionDriver finalization operations."""
from __future__ import annotations

import logging
import time
from openprogram.runtime.conversation.production_driver import shared
from openprogram.runtime.execution.control.service import RuntimeControlService
from openprogram.runtime.execution.attempts import (
    AttemptRecord,
    AttemptStore,
)
from openprogram.runtime.execution.model import (
    CommandKind,
    CommandStatus,
    TERMINAL_EXECUTION_STATUSES,
)
from openprogram.runtime.execution.model import ExecutionStatus
from openprogram.runtime.execution.store.store import ExecutionStore

_log = logging.getLogger(__name__)


FINISH_RETRIES = 8


def _readable_failure(result: shared.Any) -> str | None:
    error = result.get("error") if isinstance(result, shared.Mapping) else getattr(result, "error", None)
    return str(error) if error else None


class FinalizationOperations:
    def _finish_attempt(
        self,
        attempt: AttemptRecord,
        result: shared.Any,
        cancel_event: shared.threading.Event,
        *,
        failure_reason: str | None = None,
    ) -> None:
        key = (attempt.execution_id, attempt.attempt_id, attempt.generation)
        if getattr(result, "_execution_safe_point_handoff", False):
            return
        with self._handles_lock:
            if key in self._finished:
                return
        service = self._control_service()
        for _ in range(FINISH_RETRIES):
            execution = service.executions.get_execution(attempt.execution_id)
            if execution is None or execution.status in TERMINAL_EXECUTION_STATUSES:
                self._settle_finish(key, result)
                return
            if (
                execution.status is ExecutionStatus.PAUSED
                or execution.current_attempt_id != attempt.attempt_id
                or execution.owner_lease.get("generation") != attempt.generation
            ):
                # A safe point already ended this owner; its late return
                # must not manufacture a terminal completion.
                return
            cancelled = cancel_event.is_set() or execution.status is ExecutionStatus.CANCELLING
            if cancelled and execution.status is not ExecutionStatus.CANCELLING:
                # terminate() is only a physical signal. Without a recorded
                # cancelling intent, owner recovery is the terminal path.
                self._recover_owner_loss(attempt)
                return
            failed = bool(getattr(result, "failed", False))
            if isinstance(result, shared.Mapping):
                failed = failed or bool(
                    result.get("error") or result.get("killed") or result.get("page_cleanup_failed")
                )
            if cancelled:
                target, outcome = ExecutionStatus.CANCELLED, "cancelled"
                reason_code = execution.reason_code or "cancelled"
            elif failed:
                target, outcome = ExecutionStatus.FAILED, "failed"
                reason_code = failure_reason or "agent_runner_error"
            else:
                target, outcome, reason_code = ExecutionStatus.COMPLETED, "completed", None
            failure_text = None if cancelled or not failed else _readable_failure(result)
            with self._handles_lock:
                command_id = self._cancel_commands.get(key)
            if cancelled:
                # A cancel may land after this owner computed its result.
                command_id = self._current_cancel_command_id(
                    service.executions, execution.execution_id, fallback=command_id,
                )
            try:
                service.finish_attempt(
                    attempt_id=attempt.attempt_id,
                    generation=attempt.generation,
                    expected_execution_version=execution.status_version,
                    target=target,
                    outcome=outcome,
                    command_id=command_id,
                    reason_code=reason_code,
                    failure_text=failure_text,
                )
            except Exception:
                # Usually the execution moved under this owner, such as a
                # cancel landing now; decide again from what it is.
                _log.debug("finish of %s will be decided again", attempt.execution_id, exc_info=True)
                time.sleep(0.05)
                continue
            self._settle_finish(key, result)
            return
        _log.error("execution %s attempt %s could not finish", attempt.execution_id, attempt.attempt_id)


    def _settle_finish(self, key: tuple[str, str, int], result: shared.Any = None) -> None:
        with self._handles_lock:
            self._finished.add(key)
            self._cancel_commands.pop(key, None)
        if self.executions is None:
            return
        execution = self.executions.get_execution(key[0])
        if execution is None or execution.status not in TERMINAL_EXECUTION_STATUSES:
            return
        if self.run_ended is not None:
            try:
                self.run_ended(self.executions, execution, result)
            except Exception:
                shared._log.exception("end of run %s not reported", key[0])
        # A busy or unavailable Goal is retried until the notice lands.
        from openprogram.programs.tools.planning.goal import chat
        chat.settle_terminal(self.executions, execution)


    @staticmethod
    def _current_cancel_command_id(
        executions: ExecutionStore,
        execution_id: str,
        *,
        fallback: str | None,
    ) -> str | None:
        """Return the applying cancel command for the current execution."""
        commands = executions.list_commands(
            execution_id,
            statuses=(CommandStatus.APPLYING,),
            kinds=(CommandKind.CANCEL,),
        )
        if commands:
            return commands[0].command_id
        if fallback:
            command = executions.get_command(fallback)
            if (
                command is not None
                and command.execution_id == execution_id
                and command.kind is CommandKind.CANCEL
                and command.status is CommandStatus.APPLYING
            ):
                return command.command_id
        return None


    def _recover_owner_loss(self, attempt: AttemptRecord) -> None:
        try:
            self._control_service().recover_owner_loss(
                attempt.execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        except Exception:
            return


    def _control_service(self) -> RuntimeControlService:
        if self.control_service is None:
            if self.executions is None:
                raise shared.AgentDriverError("store_required", "execution store is required")
            self.control_service = RuntimeControlService(
                self.executions,
                AttemptStore(self.executions),
                registry=self._new_registry(),
            )
        return self.control_service
