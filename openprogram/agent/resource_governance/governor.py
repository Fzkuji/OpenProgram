"""Resource governance: governor."""
from __future__ import annotations
import hashlib
import time
import uuid
from typing import Any, Callable
from openprogram.agent.job.types import Job
from openprogram.usage.ledger import UsageLedger
from .contracts import (
    AdmissionDecision,
)
from .limits import (
    JOB_LIMIT_FIELDS,
    LIMIT_FIELDS,
    ResolvedLimit,
    ResolvedResourceLimits,
    ResourceLimitError,
    ResourceLimits,
    _less_or_equal,
    global_resource_limits,
    resolve_resource_limits,
    session_resource_limits,
)
from .usage import (
    _empty_usage_view,
    _job_fingerprint,
    _microusd_text,
    _scope_usage_breakdown,
    _usage_view,
)
from .dispatch import DispatchOperations
from .borrowed import BorrowedOperations
from .finalization import FinalizationOperations
from .recovery import RecoveryOperations
from .reservations import ReservationsOperations


class ResourceGovernor(DispatchOperations, BorrowedOperations, FinalizationOperations, RecoveryOperations, ReservationsOperations):
    """Single durable admission boundary backed by the usage SQLite DB."""

    def __init__(
        self,
        ledger: UsageLedger,
        *,
        limit_resolver: Callable[[str, Job], ResolvedResourceLimits] | None = None,
        session_limit_resolver: Callable[[str], ResolvedResourceLimits] | None = None,
    ) -> None:
        self.ledger = ledger
        self._limit_resolver = limit_resolver or self._resolve_limits
        if session_limit_resolver is not None:
            self._session_limit_resolver = session_limit_resolver
        elif limit_resolver is None:
            self._session_limit_resolver = self._resolve_session_limits
        else:
            self._session_limit_resolver = lambda session_id: self._limit_resolver(
                session_id,
                Job(id="", parent_session_id=session_id, prompt="", agent_id=""),
            )


    @staticmethod
    def _resolve_limits(session_id: str, job: Job) -> ResolvedResourceLimits:
        # After admission this field is the resolved snapshot persisted on
        # Job, not a new child-limit request. Replaying that snapshot as
        # input would treat session-only capacity as an illegal job limit
        # and break idempotent dispatch/resume.
        job_limits = ResourceLimits.from_mapping(
            {} if job.admission_id else (job.effective_limits or {})
        )
        return resolve_resource_limits(
            global_resource_limits(), session=session_resource_limits(session_id),
            job=job_limits,
        )


    @staticmethod
    def _resolve_session_limits(session_id: str) -> ResolvedResourceLimits:
        global_limits = global_resource_limits()
        session_limits = session_resource_limits(session_id)
        clamped_session: dict[str, Any] = {}
        for name in LIMIT_FIELDS:
            session_value = getattr(session_limits, name)
            global_value = getattr(global_limits, name)
            if (
                session_value is not None
                and global_value is not None
                and not _less_or_equal(session_value, global_value)
            ):
                session_value = None
            clamped_session[name] = session_value
        return resolve_resource_limits(
            global_limits, session=ResourceLimits.from_mapping(clamped_session),
        )


    def _capacity(self, conn, session_id: str, resolved: ResolvedResourceLimits) -> dict:
        row = conn.execute(
            """SELECT
                SUM(CASE WHEN state IN ('live','stopping') THEN 1 ELSE 0 END),
                SUM(CASE WHEN state IN ('preparing','queued') THEN 1 ELSE 0 END),
                COUNT(*)
               FROM job_admissions WHERE session_id = ?""",
            (session_id,),
        ).fetchone()
        limits = resolved.effective_limits()
        configured_live_limit = limits["max_live_per_session"]
        return {
            "scheduler_capacity": resolved.scheduler_capacity,
            "session_live": {
                "used": int(row[0] or 0), "limit": configured_live_limit,
            },
            "session_queued": {"used": int(row[1] or 0), "limit": limits["max_queued_per_session"]},
            "session_jobs": {"used": int(row[2] or 0), "limit": limits["max_jobs_per_session"]},
        }


    @staticmethod
    def _session_usage(conn, session_id: str) -> dict[str, Any]:
        row = conn.execute(
            """SELECT budget_scope_id FROM budget_scopes
               WHERE scope_kind = 'session' AND session_id = ?""",
            (session_id,),
        ).fetchone()
        if row is None:
            return _empty_usage_view()
        return _usage_view(_scope_usage_breakdown(conn, row["budget_scope_id"]))


    @staticmethod
    def _ancestor_limits(conn, parent_job_id: str | None) -> ResourceLimits:
        if not parent_job_id:
            return ResourceLimits()
        row = conn.execute(
            """WITH RECURSIVE ancestors AS (
                   SELECT b.* FROM job_admissions a
                   JOIN budget_scopes b ON b.budget_scope_id = a.budget_scope_id
                   WHERE a.job_id = ?
                   UNION ALL
                   SELECT parent.* FROM budget_scopes parent
                   JOIN ancestors child
                     ON child.parent_scope_id = parent.budget_scope_id
               )
               SELECT MIN(max_total_tokens), MIN(max_cost_microusd),
                      MIN(max_runtime_seconds), MIN(idle_timeout_seconds)
               FROM ancestors""",
            (parent_job_id,),
        ).fetchone()
        if row is None:
            return ResourceLimits()
        return ResourceLimits(
            max_total_tokens=row[0],
            max_cost_usd=_microusd_text(row[1]) if row[1] is not None else None,
            max_runtime_seconds=row[2],
            idle_timeout_seconds=row[3],
        )


    @staticmethod
    def _with_ancestor_limits(
        resolved: ResolvedResourceLimits,
        ancestor: ResourceLimits,
    ) -> ResolvedResourceLimits:
        fields = dict(resolved.fields)
        for name in JOB_LIMIT_FIELDS:
            candidate = getattr(ancestor, name)
            current = fields[name].effective
            if candidate is not None and (
                current is None or not _less_or_equal(current, candidate)
            ):
                fields[name] = ResolvedLimit(candidate, candidate, "parent")
        return ResolvedResourceLimits(resolved.scheduler_capacity, fields)


    @staticmethod
    def _denied(
        reason_code: str,
        *,
        resolved: ResolvedResourceLimits,
        capacity: dict[str, Any],
        retryable: bool,
        usage: dict[str, Any] | None = None,
    ) -> AdmissionDecision:
        return AdmissionDecision(
            accepted=False,
            job_id=None,
            reason_code=reason_code,
            retryable=retryable,
            effective_limits=resolved.to_dict(),
            capacity=capacity,
            usage=usage or _empty_usage_view(),
        )


    def admit_job(
        self,
        job: Job,
        *,
        persist: Callable[[Job], Any],
        creates_agent: bool = True,
        caller_session_id: str | None = None,
        caller_turn_id: str | None = None,
        dispatch_ready: bool = True,
        borrowed_claim: tuple[str, str, int] | None = None,
    ) -> AdmissionDecision:
        try:
            resolved = self._limit_resolver(job.parent_session_id, job)
        except ResourceLimitError:
            fallback = resolve_resource_limits(ResourceLimits())
            try:
                with self.ledger.read() as conn:
                    usage = self._session_usage(conn, job.parent_session_id)
            except Exception:
                return self._denied(
                    "quota.accounting_unavailable",
                    resolved=fallback,
                    capacity={"scheduler_capacity": fallback.scheduler_capacity},
                    retryable=True,
                )
            return self._denied(
                "quota.invalid_limits", resolved=fallback,
                capacity={"scheduler_capacity": fallback.scheduler_capacity},
                retryable=False,
                usage=usage,
            )
        fingerprint = _job_fingerprint(job)
        admission_id = job.admission_id or "adm_" + uuid.uuid4().hex
        scope_id = "budget_" + uuid.uuid4().hex
        session_scope_id = "session_" + hashlib.sha256(
            job.parent_session_id.encode("utf-8")
        ).hexdigest()[:24]
        session_effective = self._session_limit_resolver(
            job.parent_session_id
        ).effective_limits()
        spawn_depth_limit = spawn_fanout_limit = 0
        if creates_agent:
            from openprogram.programs.tools.agents.agent.agent.agent import (
                max_spawn_depth,
                max_spawn_fanout,
            )
            spawn_depth_limit = max_spawn_depth()
            spawn_fanout_limit = max_spawn_fanout()

        with self.ledger.immediate() as conn:
            resolved = self._with_ancestor_limits(
                resolved, self._ancestor_limits(conn, job.parent_job_id),
            )
            effective = resolved.effective_limits()
            existing = conn.execute(
                """SELECT admission_id, request_fingerprint, budget_scope_id, state
                   FROM job_admissions WHERE job_id = ?""",
                (job.id,),
            ).fetchone()
            capacity = self._capacity(conn, job.parent_session_id, resolved)
            usage = self._session_usage(conn, job.parent_session_id)
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    return self._denied(
                        "quota.admission_conflict", resolved=resolved,
                        capacity=capacity, retryable=False, usage=usage,
                    )
                job.admission_id = existing["admission_id"]
                job.budget_scope_id = existing["budget_scope_id"]
                job.effective_limits = effective
                job.resolved_limits_snapshot = resolved.to_dict()
                return AdmissionDecision(
                    accepted=True,
                    job_id=job.id,
                    reason_code=None,
                    retryable=False,
                    effective_limits=resolved.to_dict(),
                    capacity=capacity,
                    idempotent=True,
                    usage=usage,
                )
            borrowed_parent_job_id = None
            if borrowed_claim is not None:
                parent_job_id, owner_instance_id, lease_generation = borrowed_claim
                parent_claim = conn.execute(
                    """SELECT session_id FROM job_admissions
                       WHERE job_id = ? AND state IN ('live','stopping')
                         AND owner_instance_id = ? AND lease_generation = ?""",
                    (parent_job_id, owner_instance_id, lease_generation),
                ).fetchone()
                if (
                    parent_claim is None
                    or parent_claim["session_id"] != job.parent_session_id
                ):
                    return self._denied(
                        "quota.parent_claim_unavailable",
                        resolved=resolved,
                        capacity=capacity,
                        retryable=True,
                        usage=usage,
                    )
                borrowed_parent_job_id = parent_job_id
            if (
                spawn_depth_limit
                and job.chain_generations > spawn_depth_limit
            ):
                return self._denied(
                    "quota.spawn_depth", resolved=resolved,
                    capacity=capacity, retryable=False, usage=usage,
                )
            if creates_agent and caller_turn_id and spawn_fanout_limit:
                fanout_session_id = (
                    caller_session_id
                    or job.caller_session_id
                    or job.parent_session_id
                )
                fanout_used = conn.execute(
                    """SELECT COUNT(*) FROM job_admissions
                       WHERE COALESCE(caller_session_id, session_id) = ?
                         AND caller_turn_id = ?
                         AND creates_agent = 1""",
                    (fanout_session_id, caller_turn_id),
                ).fetchone()[0]
                if fanout_used >= spawn_fanout_limit:
                    return self._denied(
                        "quota.spawn_fanout", resolved=resolved,
                        capacity=capacity, retryable=False, usage=usage,
                    )
            queued = capacity["session_queued"]
            if queued["limit"] is not None and queued["used"] >= queued["limit"]:
                return self._denied(
                    "quota.queue_full", resolved=resolved,
                    capacity=capacity, retryable=True, usage=usage,
                )
            cumulative = capacity["session_jobs"]
            if cumulative["limit"] is not None and cumulative["used"] >= cumulative["limit"]:
                return self._denied(
                    "quota.jobs_exhausted", resolved=resolved,
                    capacity=capacity, retryable=False, usage=usage,
                )
            admitted_seq = conn.execute(
                "SELECT COALESCE(MAX(admitted_seq), 0) + 1 FROM job_admissions"
            ).fetchone()[0]
            conn.execute(
                """INSERT OR IGNORE INTO budget_scopes (
                    budget_scope_id, scope_kind, session_id, job_id,
                    max_total_tokens, max_cost_microusd,
                    max_runtime_seconds, idle_timeout_seconds, created_at
                ) VALUES (?, 'session', ?, NULL, ?, ?, ?, ?, ?)""",
                (
                    session_scope_id, job.parent_session_id,
                    session_effective["max_total_tokens"],
                    ResourceLimits.usd_to_microusd(session_effective["max_cost_usd"])
                    if session_effective["max_cost_usd"] is not None else None,
                    session_effective["max_runtime_seconds"],
                    session_effective["idle_timeout_seconds"],
                    time.time(),
                ),
            )
            conn.execute(
                """UPDATE budget_scopes SET
                    max_total_tokens = ?, max_cost_microusd = ?
                   WHERE budget_scope_id = ?""",
                (
                    session_effective["max_total_tokens"],
                    ResourceLimits.usd_to_microusd(session_effective["max_cost_usd"])
                    if session_effective["max_cost_usd"] is not None else None,
                    session_scope_id,
                ),
            )
            parent_scope = session_scope_id
            if job.parent_job_id:
                parent = conn.execute(
                    "SELECT budget_scope_id FROM job_admissions WHERE job_id = ?",
                    (job.parent_job_id,),
                ).fetchone()
                if parent is not None:
                    parent_scope = parent[0]
            conn.execute(
                """INSERT INTO budget_scopes (
                    budget_scope_id, scope_kind, session_id, job_id,
                    parent_scope_id, max_total_tokens, max_cost_microusd,
                    max_runtime_seconds, idle_timeout_seconds, created_at
                ) VALUES (?, 'job', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope_id, job.parent_session_id, job.id, parent_scope,
                    effective["max_total_tokens"]
                    if resolved.fields["max_total_tokens"].source == "job"
                    else None,
                    ResourceLimits.usd_to_microusd(effective["max_cost_usd"])
                    if (
                        effective["max_cost_usd"] is not None
                        and resolved.fields["max_cost_usd"].source == "job"
                    )
                    else None,
                    effective["max_runtime_seconds"], effective["idle_timeout_seconds"],
                    time.time(),
                ),
            )
            conn.execute(
                """INSERT INTO job_admissions (
                    admission_id, job_id, session_id, parent_job_id,
                    caller_session_id, caller_turn_id, creates_agent,
                    request_fingerprint,
                    budget_scope_id, dispatch_ready, borrowed_parent_job_id, state,
                    admitted_seq, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preparing', ?, ?)""",
                (
                    admission_id, job.id, job.parent_session_id, job.parent_job_id,
                    caller_session_id or job.caller_session_id,
                    caller_turn_id, int(creates_agent), fingerprint, scope_id,
                    int(dispatch_ready), borrowed_parent_job_id,
                    admitted_seq, time.time(),
                ),
            )

        job.admission_id = admission_id
        job.budget_scope_id = scope_id
        job.effective_limits = effective
        job.resolved_limits_snapshot = resolved.to_dict()
        job.status = job.status.__class__.QUEUED
        job.queued_at = job.queued_at or time.time()
        try:
            persist(job)
        except Exception:
            with self.ledger.immediate() as conn:
                conn.execute(
                    "DELETE FROM job_admissions WHERE admission_id = ? AND state = 'preparing'",
                    (admission_id,),
                )
                conn.execute("DELETE FROM budget_scopes WHERE budget_scope_id = ?", (scope_id,))
            raise
        with self.ledger.immediate() as conn:
            conn.execute(
                "UPDATE job_admissions SET state = 'queued' "
                "WHERE admission_id = ? AND state = 'preparing'",
                (admission_id,),
            )
            capacity = self._capacity(conn, job.parent_session_id, resolved)
            usage = self._session_usage(conn, job.parent_session_id)
        return AdmissionDecision(
            accepted=True,
            job_id=job.id,
            reason_code=None,
            retryable=False,
            effective_limits=resolved.to_dict(),
            capacity=capacity,
            usage=usage,
        )


    def _resolved_for_admission(self, conn, job_id: str):
        row = conn.execute(
            "SELECT session_id FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()
        if row is None:
            return None, None
        job = Job(
            id=job_id, parent_session_id=row["session_id"], prompt="", agent_id="",
        )
        return row, self._limit_resolver(row["session_id"], job)

