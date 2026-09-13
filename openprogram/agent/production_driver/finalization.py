"""AgentProductionDriver finalization operations."""
from __future__ import annotations
from . import shared


class FinalizationOperations:
    def _finish_attempt(
        self,
        attempt: shared.AttemptRecord,
        result: shared.Any,
        cancel_event: shared.threading.Event,
        *,
        failure_reason: str | None = None,
    ) -> None:
        key = (attempt.execution_id, attempt.attempt_id, attempt.generation)
        if getattr(result, "_execution_safe_point_handoff", False):
            return
        with self._handles_lock:
            already_finished = (
                key in self._finished or key in self._finish_repair_stalled
            )
        if already_finished:
            # A duplicate completion can arrive after the durable transition
            # succeeded. It must also resolve any in-process or persisted
            # repair state associated with that exact owner.
            self._resolve_finish_retry(key)
            return
        service = self._control_service()
        execution = service.executions.get_execution(attempt.execution_id)
        if execution is None or execution.status in shared.TERMINAL_EXECUTION_STATUSES:
            self._resolve_finish_retry(key)
            return
        if (
            execution.status is shared.ExecutionStatus.PAUSED
            or execution.current_attempt_id != attempt.attempt_id
            or execution.owner_lease.get("generation") != attempt.generation
        ):
            # A successful safe-point transaction ended this exact owner.
            # Its producer may finish afterwards; that late return must not
            # manufacture a terminal completion or repair record.
            return
        cancelled = cancel_event.is_set() or execution.status is shared.ExecutionStatus.CANCELLING
        if cancelled and execution.status is not shared.ExecutionStatus.CANCELLING:
            # terminate() is only a physical signal. Without a durable
            # cancelling intent the canonical service cannot legally move a
            # running execution directly to cancelled; owner recovery is the
            # safe terminal path.
            self._recover_owner_loss(attempt)
            return
        failed = bool(getattr(result, "failed", False))
        if isinstance(result, shared.Mapping):
            failed = failed or bool(
                result.get("error") or result.get("killed") or result.get("page_cleanup_failed")
            )
        target = (
            shared.ExecutionStatus.CANCELLED
            if cancelled
            else shared.ExecutionStatus.FAILED
            if failed
            else shared.ExecutionStatus.COMPLETED
        )
        outcome = "cancelled" if cancelled else "failed" if failed else "completed"
        if failed and failure_reason is None:
            failure_reason = "agent_runner_error"
        reason_code = (
            execution.reason_code or "cancelled"
            if cancelled
            else failure_reason
            if failed
            else None
        )
        with self._handles_lock:
            cancel_command_id = self._cancel_commands.get(key)
        try:
            service.finish_attempt(
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
                expected_execution_version=execution.status_version,
                target=target,
                outcome=outcome,
                command_id=cancel_command_id,
                reason_code=reason_code,
            )
        except Exception:
            # Keep the completion eligible for a later recovery/retry. A
            # transient persistence failure must not be hidden by marking
            # the attempt finished before the durable transition succeeds.
            self._queue_finish_retry(
                attempt,
                execution.status_version,
                target,
                outcome,
                reason_code,
                cancel_command_id,
            )
            return
        self._resolve_finish_retry(key)


    def _queue_finish_retry(
        self,
        attempt: shared.AttemptRecord,
        expected_execution_version: int,
        target: shared.ExecutionStatus,
        outcome: str,
        reason_code: str | None,
        command_id: str | None,
    ) -> None:
        key = (attempt.execution_id, attempt.attempt_id, attempt.generation)
        with self._handles_lock:
            self._pending_finishes[key] = (
                attempt, expected_execution_version, target, outcome, reason_code,
                command_id,
            )
        if self.executions is not None:
            self._persist_finish_retry(
                attempt, expected_execution_version, target, outcome,
                reason_code, command_id,
            )
        self._schedule_finish_retry_worker()


    def _retry_finish(self, key: tuple[str, str, int]) -> None:
        delay = 0.05
        retries = 0
        while True:
            retries += 1
            if retries > shared.FINISH_RETRY_LIMIT:
                self._stall_finish_retry(key)
                return
            try:
                self._handles_lock.acquire()
                pending = self._pending_finishes.get(key)
                if pending is None or key in self._finished:
                    self._handles_lock.release()
                    return
                (
                    attempt, _expected_version, target, outcome, reason_code,
                    command_id,
                ) = pending
                self._handles_lock.release()
                try:
                    service = self._control_service()
                    execution = service.executions.get_execution(attempt.execution_id)
                except Exception:
                    shared.time.sleep(delay)
                    delay = min(delay * 2, 1.0)
                    continue
                if execution is None or execution.status in shared.TERMINAL_EXECUTION_STATUSES:
                    self._resolve_finish_retry(key)
                    return
                if (
                    execution.current_attempt_id != attempt.attempt_id
                    or execution.owner_lease.get("generation") != attempt.generation
                ):
                    # A newer owner won the fence. The old completion must not
                    # overwrite it; the canonical record is already owned by
                    # recovery or the replacement attempt.
                    self._resolve_finish_retry(key)
                    return
                current_attempt = service.attempts.get(attempt.attempt_id)
                if (
                    current_attempt is None
                    or current_attempt.status is not shared.AttemptStatus.ACTIVE
                    or current_attempt.generation != attempt.generation
                ):
                    self._resolve_finish_retry(key)
                    return
                retry_target = target
                retry_outcome = outcome
                retry_reason = reason_code
                if execution.status is shared.ExecutionStatus.CANCELLING:
                    # Cancellation intent wins over a completion that was
                    # computed before the cancel CAS. Re-read the version on
                    # every attempt so the finish cannot reuse stale state.
                    retry_target = shared.ExecutionStatus.CANCELLED
                    retry_outcome = "cancelled"
                    retry_reason = execution.reason_code or "cancelled"
                    # A finish may have failed before the cancel request was
                    # associated with this owner. Resolve the currently
                    # applying canonical cancel command before finishing, so
                    # the command cannot remain APPLYING after cancellation.
                    command_id = self._current_cancel_command_id(
                        service.executions,
                        execution.execution_id,
                        fallback=command_id,
                    )
                    if command_id is None:
                        shared.time.sleep(delay)
                        delay = min(delay * 2, shared.FINISH_RETRY_MAX_DELAY)
                        continue
                    with self._handles_lock:
                        current_pending = self._pending_finishes.get(key)
                        if current_pending is not None:
                            self._pending_finishes[key] = (
                                current_pending[0],
                                current_pending[1],
                                current_pending[2],
                                current_pending[3],
                                current_pending[4],
                                command_id,
                            )
                self._persist_finish_retry(
                    attempt,
                    execution.status_version,
                    retry_target,
                    retry_outcome,
                    retry_reason,
                    command_id,
                )
                try:
                    service.finish_attempt(
                        attempt_id=attempt.attempt_id,
                        generation=attempt.generation,
                        expected_execution_version=execution.status_version,
                        target=retry_target,
                        outcome=retry_outcome,
                        command_id=command_id,
                        reason_code=retry_reason,
                    )
                except shared.AttemptConflict:
                    current = service.executions.get_execution(attempt.execution_id)
                    if (
                        current is None
                        or current.status in shared.TERMINAL_EXECUTION_STATUSES
                        or current.current_attempt_id != attempt.attempt_id
                        or current.owner_lease.get("generation") != attempt.generation
                    ):
                        self._resolve_finish_retry(key)
                        return
                except Exception:
                    pass
                else:
                    self._resolve_finish_retry(key)
                    return
                shared.time.sleep(delay)
                delay = min(delay * 2, shared.FINISH_RETRY_MAX_DELAY)
            finally:
                pass


    def _persist_finish_retry(
        self,
        attempt: shared.AttemptRecord,
        expected_execution_version: int,
        target: shared.ExecutionStatus,
        outcome: str,
        reason_code: str | None,
        command_id: str | None,
        retry_count: int = 0,
        next_attempt_at: float = 0.0,
    ) -> bool:
        if self.executions is None:
            return False
        try:
            self.executions.upsert_finish_repair(
                execution_id=attempt.execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
                expected_version=expected_execution_version,
                target=target.value,
                outcome=outcome,
                reason_code=reason_code,
                command_id=command_id,
                retry_count=retry_count,
                next_attempt_at=next_attempt_at,
            )
            with self._handles_lock:
                self._finish_repair_metrics["persisted"] += 1
            return True
        except Exception:
            # The bounded retry keeps the intent in memory and retries this
            # write on every pass; the single repair worker continues after
            # the initial eight attempts without creating one thread per key.
            with self._handles_lock:
                self._finish_repair_metrics["write_errors"] += 1
            return False


    def _stall_finish_retry(self, key: tuple[str, str, int]) -> None:
        with self._handles_lock:
            pending = self._pending_finishes.get(key)
        if pending is None:
            return
        attempt, expected_version, target, outcome, _reason_code, command_id = pending
        persisted = self._persist_finish_retry(
            attempt,
            expected_version,
            target,
            outcome,
            "finish_repair_stalled",
            command_id,
            retry_count=shared.FINISH_RETRY_LIMIT,
            next_attempt_at=shared.time.time() + shared.FINISH_REPAIR_RETRY_TIMER_DELAY,
        )
        if not persisted:
            shared._log.error(
                "finish repair %s/%s/%s remains pending after retry budget; "
                "durable write is unavailable",
                attempt.execution_id,
                attempt.attempt_id,
                attempt.generation,
            )
            return
        with self._handles_lock:
            self._pending_finishes.pop(key, None)
            self._finish_repair_stalled.add(key)
            self._finished.add(key)
            self._finish_repair_metrics["stalled"] += 1


    def _schedule_finish_retry_worker(self) -> None:
        with self._handles_lock:
            if self._finish_retry_worker_active:
                return
            self._finish_retry_worker_active = True
        shared.threading.Thread(
            target=self._run_finish_retry_worker,
            name="openprogram-agent-finish-repair",
            daemon=True,
        ).start()


    def _run_finish_retry_worker(self) -> None:
        try:
            with self._handles_lock:
                keys = tuple(self._pending_finishes)
            for key in keys:
                self._retry_finish(key)
            self._reconcile_stalled_repairs()
        finally:
            with self._handles_lock:
                self._finish_retry_worker_active = False
                pending = bool(self._pending_finishes)
            stalled = self.executions is not None and self.executions.has_stalled_finish_repairs()
            if pending or stalled:
                with self._handles_lock:
                    if self._finish_retry_timer is None:
                        timer = shared.threading.Timer(
                            shared.FINISH_REPAIR_RETRY_TIMER_DELAY,
                            self._finish_retry_timer_fired,
                        )
                        timer.daemon = True
                        self._finish_retry_timer = timer
                        timer.start()


    def _reconcile_stalled_repairs(self) -> None:
        if self.executions is None:
            return
        try:
            self._control_service().replay_finish_repairs(
                include_stalled=True, due_only=True,
            )
        except Exception:
            shared._log.exception("stalled Agent finish repair reconciliation failed")


    def _finish_retry_timer_fired(self) -> None:
        with self._handles_lock:
            self._finish_retry_timer = None
        self._schedule_finish_retry_worker()


    def _resolve_finish_retry(self, key: tuple[str, str, int]) -> None:
        self._delete_persisted_finish(key)
        with self._handles_lock:
            self._pending_finishes.pop(key, None)
            self._finish_repair_stalled.discard(key)
            self._finished.add(key)
            self._cancel_commands.pop(key, None)


    @staticmethod
    def _current_cancel_command_id(
        executions: shared.ExecutionStore,
        execution_id: str,
        *,
        fallback: str | None,
    ) -> str | None:
        """Return the applying cancel command for the current execution."""
        commands = executions.list_commands(
            execution_id,
            statuses=(shared.CommandStatus.APPLYING,),
            kinds=(shared.CommandKind.CANCEL,),
        )
        if commands:
            return commands[0].command_id
        if fallback:
            command = executions.get_command(fallback)
            if (
                command is not None
                and command.execution_id == execution_id
                and command.kind is shared.CommandKind.CANCEL
                and command.status is shared.CommandStatus.APPLYING
            ):
                return command.command_id
        return None


    def _delete_persisted_finish(self, key: tuple[str, str, int]) -> None:
        execution_id, attempt_id, generation = key
        if self.executions is not None:
            try:
                self.executions.delete_finish_repair(
                    execution_id, attempt_id, generation,
                )
            except Exception:
                pass


    def _recover_owner_loss(self, attempt: shared.AttemptRecord) -> None:
        try:
            self._control_service().recover_owner_loss(
                attempt.execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        except Exception:
            return


    def _control_service(self) -> shared.RuntimeControlService:
        if self.control_service is None:
            if self.executions is None:
                raise shared.AgentDriverError("store_required", "execution store is required")
            self.control_service = shared.RuntimeControlService(
                self.executions,
                shared.AttemptStore(self.executions),
                registry=self._new_registry(),
            )
        return self.control_service

