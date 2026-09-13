"""JobRunner service operations."""
from __future__ import annotations
from . import shared
from .admission import AdmissionOperations
from .projection import ProjectionOperations
from .borrowed import BorrowedOperations
from .cancellation import CancellationOperations
from .dispatch import DispatchOperations
from .recovery import RecoveryOperations
from .progress import ProgressOperations


class JobRunner(AdmissionOperations, ProjectionOperations, BorrowedOperations, CancellationOperations, DispatchOperations, RecoveryOperations, ProgressOperations):
    """Singleton job pool. Use :func:`get_runner`.

    Public surface:

      * :meth:`spawn_job` — submit, return job_id
      * :meth:`cancel_execution` — set cancel event, schedule timeout,
        cascade to descendant jobs (parent_job_id chain)
      * :meth:`get_job` / :meth:`list_jobs` — read
      * :meth:`await_job` — block until terminal, return final Job

    The runner is *thread-safe* — all maps are guarded by
    ``self._lock``.
    """


    def __init__(
        self,
        max_workers: shared.Optional[int] = None,
        *,
        governor=None,
        monotonic_clock: shared.Callable[[], float] | None = None,
        budget_poll_seconds: float = 0.25,
        agent_driver_factory: shared.Callable[[shared.Any, shared.Any], shared.Any] | None = None,
    ) -> None:
        if max_workers is None:
            try:
                max_workers = int(
                    shared.os.environ.get("OPENPROGRAM_JOB_WORKERS")
                    or shared._DEFAULT_MAX_WORKERS
                )
            except ValueError:
                max_workers = shared._DEFAULT_MAX_WORKERS
        if max_workers < 1:
            max_workers = 1
        self.max_workers = max_workers
        self._owns_governor = governor is None
        if governor is None:
            from openprogram.agent.resource_governance import ResourceGovernor
            from openprogram.store import default_store
            from openprogram.usage.ledger import UsageLedger

            governor = ResourceGovernor(
                UsageLedger(default_store().root_path.parent / "usage.db")
            )
        self._governor = governor
        self._agent_driver_factory = agent_driver_factory
        self._monotonic = monotonic_clock or shared.time.monotonic
        self._budget_poll_seconds = budget_poll_seconds
        # Canonical execution state is authoritative for public Jobs.  The
        # legacy JobStore remains a projection for existing surfaces.
        from openprogram.execution import (
            AttemptStore,
            DriverRegistry,
            RuntimeControlService,
        )
        from openprogram.execution.store import default_store as default_execution_store
        self._execution_store = default_execution_store()
        with shared._RUNNERS_BY_EXECUTION_LOCK:
            shared._RUNNERS_BY_EXECUTION_PATH[str(self._execution_store.path)] = self
        self._execution_attempts = AttemptStore(self._execution_store)
        self._execution_registry = DriverRegistry()
        self._execution_control = RuntimeControlService(
            self._execution_store,
            self._execution_attempts,
            self._execution_registry,
            owner_id=f"job-control-{shared.os.getpid()}",
            cancel_grace_seconds=shared._CANCEL_ESCALATION_SECS,
        )
        self._instance_id = f"worker_{shared.os.getpid()}_{shared.uuid.uuid4().hex}"
        from openprogram.execution.resource_saga import ResourceSaga
        from openprogram.execution.waits import DurableWaitStore

        self._resource_saga = ResourceSaga(
            self._execution_store,
            self._governor,
            owner_id=self._instance_id,
        )
        self._execution_waits = DurableWaitStore(self._execution_store)
        self._wait_recovery_tasks: set[shared.asyncio.Task] = set()
        # Every canonical terminal transition, including a transport-neutral
        # cancel command, must converge the JobStore projection and release
        # its admission.  The observer is attached before startup recovery so
        # recovery-generated terminal states use the same path.
        self._execution_control.set_terminal_observer(
            self._project_canonical_terminal,
        )
        self._execution_control.set_pause_observer(
            self._release_paused_canonical_resource,
        )
        self._execution_control.set_terminal_preparer(
            self._prepare_canonical_terminal,
        )
        self._execution_control.set_terminal_recovery(
            self._recover_canonical_terminal_prepare,
        )
        self._execution_control.set_wait_suspension_observer(
            lambda suspension: self.release_paused_job_resource(
                suspension.execution.execution_id,
                reason_code="wait.safe_point",
            ),
        )
        self._execution_control.set_wait_resume_scheduler(
            self._queue_wait_resume,
        )
        self._claim_only_job_id: str | None = None
        self._claim_scope_lock = shared.threading.Lock()
        self._dispatch_wake = shared.threading.Event()
        self._shutdown_event = shared.threading.Event()
        # Canonical recovery is authoritative and startup-fatal.  The
        # projection/legacy reconciliation below must not hide a canonical
        # recovery failure.
        from openprogram.execution import recover_execution_startup
        startup_recovery = recover_execution_startup(
            control_service=self._execution_control,
        )
        # Reconcile orphans before opening the pool so any "running"
        # job from a previous process is flipped to errored. The
        # state-machine transition rules cover (running, errored).
        try:
            legacy_orphans: list[shared.Job] = []
            shared._store_reconcile(
                legacy_only=True,
                on_reconciled=legacy_orphans.append,
            )
            for orphan in legacy_orphans:
                self._broadcast_job_status(orphan)
                self._update_attach_card(orphan, error_text=orphan.error)
        except Exception:
            pass
        self._reconcile_resources()
        self._resource_saga.reconcile()
        self._migrate_orphan_job_projections()
        self._recover_orphan_canonical_jobs()
        for recovery in startup_recovery.canonical:
            if getattr(recovery.execution.status, "value", None) in {
                "completed", "cancelled", "failed", "interrupted",
            }:
                self._project_canonical_terminal(recovery.execution)
        self._pool = shared.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="op-job",
        )
        self._lock = shared.threading.Lock()
        # job_id → {"event": Event, "future": Future, "session_id": str}
        self._jobs: dict[str, dict[str, shared.Any]] = {}
        # job_id → threading.Event used to wake await_job() callers.
        self._done_events: dict[str, shared.threading.Event] = {}
        # delivery session id → lock serialising follow-up turns on that
        # session. Two sub-agents finishing at once each want to append at
        # HEAD; without this they read the same HEAD and write siblings.
        # See ``_dispatch_followup``.
        self._followup_locks: dict[str, shared.threading.Lock] = {}
        self._project_existing_canonical_terminals()
        self._executor_slots = shared.threading.BoundedSemaphore(max_workers)
        self._dispatcher_thread = shared.threading.Thread(
            target=self._dispatch_loop,
            daemon=True,
            name="op-job-dispatcher",
        )
        self._dispatcher_thread.start()
        self._reconciler_thread = shared.threading.Thread(
            target=self._reconcile_loop,
            daemon=True,
            name="op-job-reconciler",
        )
        self._reconciler_thread.start()
        self._budget_thread = shared.threading.Thread(
            target=self._budget_loop,
            daemon=True,
            name="op-job-budget",
        )
        self._budget_thread.start()
        # A new runner may inherit dispatch-ready jobs from the durable
        # admission ledger.  Trigger the first scan once initialization is
        # complete instead of depending on the dispatch loop's 500 ms
        # fallback poll.
        self._dispatch_wake.set()


    def _governance_context(self, job: shared.Job) -> shared.JobGovernanceContext:
        ledger = self._governor.ledger
        canonical_limits = getattr(self._governor, "canonical_limits", None)
        effective_limits = (
            canonical_limits(job.id)
            if canonical_limits is not None else (job.effective_limits or {})
        )
        return shared.JobGovernanceContext(
            job_id=job.id,
            budget_scope_id=job.budget_scope_id or "",
            governor=self._governor,
            ledger_identity=str(ledger._path().resolve()),
            effective_limits=tuple(sorted(effective_limits.items())),
            deadline_callback=lambda declared: self.bounded_operation_timeout(
                job.id, declared,
            ),
            activity_callback=lambda kind: self.record_job_activity(job.id, kind),
        )


    def _followup_lock(self, session_id: str) -> shared.threading.Lock:
        """The per-session follow-up lock, created on first use."""
        with self._lock:
            lk = self._followup_locks.get(session_id)
            if lk is None:
                lk = shared.threading.Lock()
                self._followup_locks[session_id] = lk
            return lk


    def get_job(self, job_id: str) -> shared.Optional[shared.Job]:
        sid = self._find_session_for_job(job_id)
        if not sid:
            return None
        return shared._store_load(sid, job_id)


    def get_job_resource_view(self, job_id: str):
        """Return the canonical resource DTO for one persisted Job."""
        job = self.get_job(job_id)
        if job is None:
            return None
        from openprogram.agent.resource_governance import build_job_resource_view

        view = build_job_resource_view(
            job,
            ledger=self._governor.ledger,
            resolved=self._governor._limit_resolver(
                job.parent_session_id, job,
            ),
        )
        execution = self._execution_store.get_execution(job_id)
        if execution is None:
            return None
        row = self._governor.ledger.connection().execute(
            "SELECT admission_id, state, queue_state, resume_command_id, "
            "lease_generation, owner_instance_id FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        pending_claim = any(
            intent["kind"] == "resource.claim.intent"
            for intent in self._execution_store.list_resource_intents(
                execution_id=job_id, states=("pending", "claimed"),
            )
        )
        from openprogram.execution.model import CommandKind, CommandStatus

        pending_resume = next(
            (
                command
                for command in self._execution_store.list_commands(
                    job_id,
                    statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING),
                )
                if command.kind in {CommandKind.CONTINUE, CommandKind.STEP}
            ),
            None,
        )
        queue_wait = None
        if row is not None and row["state"] == "queued":
            queue_state = row["queue_state"] or "queued"
            queue_wait = {
                "state": queue_state,
                "reason_code": job.reason_code,
                "since": execution.updated_at,
                "position": view.capacity.get("queue_position"),
            }
        elif pending_claim:
            queue_wait = {
                "state": "queued_resume",
                "reason_code": execution.reason_code,
                "since": execution.updated_at,
                "position": view.capacity.get("queue_position"),
            }
        elif pending_resume is not None and execution.status.value in {"paused", "queued"}:
            # The execution command and resource admission live in separate
            # durable stores.  If reconciliation moves between the two reads,
            # the accepted command is the stable proof that this execution is
            # still waiting for claim/activation rather than simply released.
            queue_wait = {
                "state": "paused_waiting_claim",
                "reason_code": execution.reason_code,
                "since": execution.updated_at,
                "position": view.capacity.get("queue_position"),
            }
        resource_state = view.resource_state
        if resource_state == "released" and queue_wait is not None:
            resource_state = queue_wait["state"]
        resource = {
            "admission_id": row["admission_id"] if row is not None else job.admission_id,
            "resource_state": resource_state,
            "queue_wait": queue_wait,
            "resource_lease_generation": row["lease_generation"] if row is not None else None,
            "owner_instance_id": row["owner_instance_id"] if row is not None else None,
            "limits": view.limits,
            "usage": view.budget,
            "reservation": None,
        }
        from openprogram.execution.public import job_resource_dto

        return job_resource_dto(
            job,
            execution=execution,
            resource=resource,
            store=self._execution_store,
        )


    def release_paused_job_resource(
        self, execution_id: str, *, reason_code: str,
        attempt_id: str | None = None, generation: int | None = None,
        resource_lease_generation: int | None = None,
    ) -> None:
        """Persist and consume the exact release intent after a queued pause."""
        job = self._canonical_job(execution_id)
        if job is None or not job.admission_id:
            return
        execution = self._execution_store.get_execution(execution_id)
        if execution is None:
            return
        self._resource_saga.request_release(
            execution_id,
            admission_id=job.admission_id,
            reason_code=reason_code,
            attempt_id=attempt_id or execution.current_attempt_id,
            generation=generation if generation is not None else execution.owner_lease.get("generation"),
            resource_lease_generation=resource_lease_generation,
        )
        self._resource_saga.reconcile()


    def _release_paused_canonical_resource(self, execution) -> None:
        """Release a Job admission after any canonical paused transition."""
        self.release_paused_job_resource(
            execution.execution_id,
            reason_code="pause.canonical",
        )


    def _queue_wait_resume(self, wait, execution) -> object | None:
        """Re-admit a resolved Job wait through the normal resource queue."""
        # Conversation executions share this database but have no Job
        # admission. Delegate to the canonical control service so a JobRunner
        # reconciliation pass cannot resolve their wait and leave the same
        # execution paused without an activator.
        if self._execution_store.get_job_agent_input(execution.execution_id) is None:
            from openprogram.execution.control import default_control_service

            control = default_control_service()
            if control is getattr(self, "_execution_control", None):
                return
            current = control.executions.get_execution(execution.execution_id)
            if current is None:
                return
            return control._resume_wait_if_required(wait=wait, execution=current)
        self.queue_job_resume(
            command_id=f"wait-resume:{wait.wait_id}:{wait.outcome}",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "durable-wait", "wait_id": wait.wait_id},
            step=False,
        )


    def queue_job_resume(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: dict[str, shared.Any],
        step: bool,
    ):
        """Persist a Job resume intent without leasing an attempt locally.

        The control service owns the command row; ResourceSaga later obtains a
        fenced Governor claim.  This prevents a WebSocket handler from turning
        an accepted continue into an active owner before capacity exists.
        """
        from openprogram.execution.model import CommandKind

        execution = self._execution_store.get_execution(execution_id)
        job = self._canonical_job(execution_id)
        if execution is None or job is None or not job.admission_id:
            raise RuntimeError("canonical Job admission is unavailable")
        kind = CommandKind.STEP if step else CommandKind.CONTINUE
        if step and execution.status.value == "queued":
            command = self._execution_store.accept_initial_job_step(
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                payload={},
                actor=actor,
            )
        else:
            command = self._execution_store.accept_command(
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=kind,
                payload={},
                actor=actor,
            )
        self._resource_saga.request_claim(
            execution_id,
            admission_id=job.admission_id,
            command_id=command_id,
            paused=True,
        )
        if step and execution.status.value == "queued":
            # The sole queued-step exception consumes a single provider
            # decision under the normal Agent safe-point hook.  It is not a
            # synthetic checkpoint: the command remains accepted until the
            # resource intent is claimed and the canonical owner is bound.
            self._resource_saga.reconcile()
            self._activate_initial_step(
                execution_id=execution_id,
                command_id=command_id,
            )
        else:
            self._dispatch_wake.set()
        latest = self._execution_store.get_execution(execution_id)
        assert latest is not None
        return self._execution_store.get_command(command_id) or command, latest


    def _activate_initial_step(self, *, execution_id: str, command_id: str) -> None:
        """Activate one resource-claimed queued Job step through Agent driver."""
        execution = self._execution_store.get_execution(execution_id)
        if execution is None or execution.status.value != "queued":
            return
        job = self._canonical_job(execution_id)
        if job is None or not job.admission_id:
            return
        claim = self._governor.claim_execution(
            execution_id,
            owner_instance_id=self._instance_id,
            admission_id=job.admission_id,
            command_id=command_id,
        )
        if claim is None:
            return
        try:
            activated = self._activate_claimed_job_command(
                claim, command_id, shared.threading.Event(),
            )
            if activated is None:
                return
            attempt, _running, binding = activated
            binding.handle.done.result(timeout=5.0)
        except Exception:
            shared._log.exception("queued initial Job step activation failed for %s", execution_id)


    def _broadcast_job_status(self, job: shared.Job) -> None:
        try:
            view = self.get_job_resource_view(job.id)
            resource = view.to_dict() if view is not None else None
        except Exception:
            resource = None
        shared._broadcast_job_status(job, resource)


    def list_jobs(
        self,
        session_id: shared.Optional[str] = None,
        *,
        status_filter: shared.Optional[set[shared.JobStatus]] = None,
        limit: shared.Optional[int] = None,
    ) -> list[shared.Job]:
        if session_id:
            return shared._store_list(session_id, status_filter=status_filter, limit=limit)
        # Walk every session — used by the global job panel.
        from openprogram.store import default_store
        store = default_store()
        if not store.root_path.exists():
            return []
        out: list[shared.Job] = []
        for sdir in sorted(store.root_path.iterdir()):
            if not sdir.is_dir():
                continue
            out.extend(shared._store_list(sdir.name, status_filter=status_filter))
        out.sort(key=lambda t: t.created_at or 0, reverse=True)
        if limit is not None:
            out = out[:limit]
        return out


    def await_job(self, job_id: str, timeout: shared.Optional[float] = None) -> shared.Optional[shared.Job]:
        """Block the calling thread until the job reaches terminal.

        Returns the final Job. Returns None on unknown job. Returns
        the current (possibly non-terminal) entity on timeout.
        """
        cur = self.get_job(job_id)
        if cur is None:
            return None
        if shared.is_terminal(cur.status):
            return cur
        with self._lock:
            done = self._done_events.get(job_id)
        if done is None:
            # Lost track (process restart with persisted job) — poll.
            deadline = shared.time.time() + (timeout or 60.0)
            while shared.time.time() < deadline:
                cur = self.get_job(job_id)
                if cur is not None and shared.is_terminal(cur.status):
                    return cur
                shared.time.sleep(0.5)
            return self.get_job(job_id)
        done.wait(timeout=timeout)
        return self.get_job(job_id)


    def await_job_durable(
        self,
        job_id: str,
        *,
        timeout: shared.Optional[float] = None,
        on_poll: shared.Callable[[], None] | None = None,
    ) -> shared.Optional[shared.Job]:
        """Wait on JobStore state that another worker process can update."""
        deadline = shared.time.monotonic() + timeout if timeout is not None else None
        while True:
            job = self.get_job(job_id)
            if job is None or shared.is_terminal(job.status):
                return job
            if deadline is not None and shared.time.monotonic() >= deadline:
                return job
            if on_poll is not None:
                on_poll()
            shared.time.sleep(0.05)


    def retire_external_waiter(self, job_id: str) -> None:
        """Drop local wait state after another process completed the job."""
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is None or entry.get("future") is not None:
                return
            self._jobs.pop(job_id, None)
            self._done_events.pop(job_id, None)


    @shared.contextmanager
    def claim_only(self, job_id: str):
        """Temporarily restrict this runner to one direct synchronous job."""
        with self._claim_scope_lock:
            if self._claim_only_job_id is not None:
                raise RuntimeError("a direct claim scope is already active")
            self._claim_only_job_id = job_id
            self._dispatch_wake.set()
            try:
                yield
            finally:
                self._claim_only_job_id = None
                self._dispatch_wake.set()


    def record_job_activity(self, job_id: str, activity_kind: str) -> bool:
        with self._lock:
            entry = self._jobs.get(job_id)
            lease_generation = (
                entry.get("lease_generation") if entry is not None else None
            )
        if lease_generation is None:
            return False
        recorded = self._governor.record_activity(
            job_id,
            owner_instance_id=self._instance_id,
            lease_generation=lease_generation,
            activity_kind=activity_kind,
        )
        if not recorded:
            return False
        lineage = [job_id]
        current = self.get_job(job_id)
        seen = {job_id}
        while current is not None and current.parent_job_id:
            parent_id = current.parent_job_id
            if parent_id in seen:
                break
            seen.add(parent_id)
            lineage.append(parent_id)
            current = self.get_job(parent_id)
        now = self._monotonic()
        with self._lock:
            for lineage_job_id in lineage:
                entry = self._jobs.get(lineage_job_id)
                if entry is not None and entry.get("started_monotonic") is not None:
                    entry["last_activity_monotonic"] = now
        return True


    def _finalize_job_status(
        self,
        session_id: str,
        job_id: str,
        lease_generation: int,
        status: shared.JobStatus,
        reason_code: str,
        *,
        attempt_id: str | None = None,
        attempt_generation: int | None = None,
        **fields: shared.Any,
    ) -> shared.Optional[shared.Job]:
        terminal: dict[str, shared.Job] = {}
        canonical_completion = None
        if attempt_id is not None and attempt_generation is not None:
            try:
                canonical_completion = self._finish_canonical_attempt(
                    job_id,
                    attempt_id,
                    attempt_generation,
                    status,
                    reason_code,
                )
            except Exception:
                return None
            if canonical_completion.execution.status.value == "cancelled":
                status = shared.JobStatus.CANCELLED
                reason_code = self._canonical_cancel_reason(job_id) or reason_code
        terminal_fields = shared._terminal_fields(
            status,
            reason_code,
            head_id=fields.get("head_id"),
            result_text=fields.get("result_text"),
            error=fields.get("error"),
        )
        try:
            self._governor.finalize_job(
                job_id, reason_code,
                owner_instance_id=self._instance_id,
                lease_generation=lease_generation,
                terminal_fields=terminal_fields,
                mutate=lambda staged_fields: terminal.setdefault(
                    "job", shared._store_write_terminal(
                        session_id, job_id, staged_fields,
                    ),
                ),
            )
        except ValueError:
            return None
        if canonical_completion is not None:
            self._project_canonical_terminal(
                canonical_completion.execution,
                terminal_fields=terminal_fields,
            )
        return terminal.get("job")


    def _finish_canonical_attempt(
        self,
        job_id: str,
        attempt_id: str,
        attempt_generation: int,
        status: shared.JobStatus,
        reason_code: str,
    ):
        from openprogram.execution import ExecutionStatus

        target = {
            shared.JobStatus.COMPLETED: ExecutionStatus.COMPLETED,
            shared.JobStatus.CANCELLED: ExecutionStatus.CANCELLED,
            shared.JobStatus.ERRORED: ExecutionStatus.FAILED,
        }[status]
        execution = self._execution_store.get_execution(job_id)
        if execution is None:
            raise RuntimeError(f"canonical execution {job_id!r} is missing")
        command_id = None
        cancel_command = self._execution_store.get_command(
            f"execution-cancel:{job_id}",
        )
        if (
            cancel_command is not None
            and cancel_command.status.value == "applying"
            and execution.status.value == "cancelling"
        ):
            target = ExecutionStatus.CANCELLED
            reason_code = cancel_command.payload.get("reason_code") or reason_code
            command_id = cancel_command.command_id
        return self._execution_control.finish_attempt(
            attempt_id=attempt_id,
            generation=attempt_generation,
            expected_execution_version=execution.status_version,
            target=target,
            outcome=reason_code,
            command_id=command_id,
            reason_code=reason_code,
        )


    def bounded_operation_timeout(
        self,
        job_id: str,
        declared_timeout: float | None,
        *,
        preemptibility: str = "async",
    ) -> float | None:
        if declared_timeout is not None and declared_timeout <= 0:
            raise ValueError("declared timeout must be positive")
        with self._lock:
            entry = self._jobs.get(job_id)
            snapshot = dict(entry) if entry is not None else None
        if snapshot is not None and snapshot.get("time_limits") is not None:
            runtime_limit, idle_limit = snapshot["time_limits"]
        else:
            runtime_limit, idle_limit = self._governor.job_time_limits(job_id)
        strict = runtime_limit is not None or idle_limit is not None
        if strict and preemptibility not in {"async", "process"}:
            raise shared.NonPreemptibleOperation(
                "error.nonpreemptible_operation: strict time-budget job "
                "cannot start an operation without a guaranteed stop boundary",
            )
        bounds = [] if declared_timeout is None else [float(declared_timeout)]
        if snapshot is not None and snapshot.get("started_monotonic") is not None:
            now = self._monotonic()
            if runtime_limit is not None:
                bounds.append(max(
                    0.0, float(runtime_limit) - (
                        now - snapshot["started_monotonic"]
                    ),
                ))
            if idle_limit is not None:
                bounds.append(max(
                    0.0, float(idle_limit) - (
                        now - snapshot["last_activity_monotonic"]
                    ),
                ))
        if strict and not bounds:
            raise shared.NonPreemptibleOperation(
                "strict time-budget job requires a live bounded operation",
            )
        return min(bounds) if bounds else None


    def operation_timeout_reason(
        self, job_id: str, declared_timeout: float | None,
    ) -> str | None:
        with self._lock:
            entry = self._jobs.get(job_id)
            snapshot = dict(entry) if entry is not None else None
        if snapshot is None or snapshot.get("started_monotonic") is None:
            return None
        runtime_limit, idle_limit = snapshot.get("time_limits", (None, None))
        now = self._monotonic()
        candidates: list[tuple[float, str]] = []
        if declared_timeout is not None:
            candidates.append((float(declared_timeout), "error.operation_timeout"))
        if runtime_limit is not None:
            candidates.append((max(
                0.0,
                float(runtime_limit) - (now - snapshot["started_monotonic"]),
            ), "budget.runtime_exhausted"))
        if idle_limit is not None:
            candidates.append((max(
                0.0,
                float(idle_limit) - (now - snapshot["last_activity_monotonic"]),
            ), "budget.idle_exhausted"))
        return min(candidates, default=(0.0, None), key=lambda item: item[0])[1]


    def shutdown(self, wait: bool = True) -> None:
        """Tear down the pool. Used in tests / process shutdown."""
        self._shutdown_event.set()
        self._dispatch_wake.set()
        self._dispatcher_thread.join(timeout=1.0)
        self._reconciler_thread.join(timeout=1.0)
        self._budget_thread.join(timeout=1.0)
        try:
            self._pool.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            # Python 3.8 fallback (no cancel_futures kwarg).
            self._pool.shutdown(wait=wait)
        finally:
            with shared._RUNNERS_BY_EXECUTION_LOCK:
                if shared._RUNNERS_BY_EXECUTION_PATH.get(str(self._execution_store.path)) is self:
                    shared._RUNNERS_BY_EXECUTION_PATH.pop(str(self._execution_store.path), None)
            if self._owns_governor:
                try:
                    self._governor.ledger.close()
                except Exception:
                    pass

