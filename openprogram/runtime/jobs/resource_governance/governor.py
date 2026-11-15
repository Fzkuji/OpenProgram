"""Resource governance: governor."""
from __future__ import annotations
import hashlib
import time
import uuid
from typing import Any, Callable
from openprogram.runtime.jobs.types import Job
from openprogram.models.usage.ledger import UsageLedger
from openprogram.runtime.jobs.resource_governance.contracts import (
    AdmissionDecision,
)
from openprogram.runtime.jobs.resource_governance.limits import (
    JOB_LIMIT_FIELDS,
    LIMIT_FIELDS,
    ResolvedLimit,
    ResolvedResourceLimits,
    ResourceLimitError,
    ResourceLimits,
    _less_or_equal,
    global_resource_limits,
    resolve_resource_limits,
    running_agents_default,
    session_resource_limits,
)
from openprogram.runtime.jobs.resource_governance.usage import (
    _empty_usage_view,
    _job_fingerprint,
    _job_scope_chain,
    _microusd_text,
    _scope_usage_breakdown,
    _smallest,
    _usage_view,
)
from openprogram.runtime.jobs.resource_governance.dispatch import DispatchOperations
from openprogram.runtime.jobs.resource_governance.borrowed import BorrowedOperations
from openprogram.runtime.jobs.resource_governance.finalization import FinalizationOperations
from openprogram.runtime.jobs.resource_governance.recovery import RecoveryOperations
from openprogram.runtime.jobs.resource_governance.reservations import ReservationsOperations
from openprogram.runtime.jobs.resource_governance.tables import TablesAccess


class ResourceGovernor(TablesAccess, DispatchOperations, BorrowedOperations, FinalizationOperations, RecoveryOperations, ReservationsOperations):
    """Single durable admission boundary, held in the profile's run tables."""

    @staticmethod
    def admission_fingerprint(job: Job) -> str:
        """The identity facts of an admitted Job, for the execution saga."""
        return _job_fingerprint(job)

    @staticmethod
    def admission_job(record: dict[str, Any]) -> Job:
        """Rebuild the admitted Job from the saga's stored record."""
        return Job.from_dict(dict(record))

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
            job=job_limits, default_live_limit=running_agents_default(),
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
            default_live_limit=running_agents_default(),
        )


    def _capacity(self, tables, session_id: str, resolved: ResolvedResourceLimits) -> dict:
        tables.tables.ensure_session(session_id)
        admissions = tables.rows("job_admissions", session_id=session_id)
        limits = resolved.effective_limits()
        configured_live_limit = limits["max_live_per_session"]
        return {
            "scheduler_capacity": resolved.scheduler_capacity,
            "session_live": {
                "used": sum(row["state"] in ("live", "stopping") for row in admissions),
                "limit": configured_live_limit,
            },
            "session_queued": {
                "used": sum(row["state"] in ("preparing", "queued") for row in admissions),
                "limit": limits["max_queued_per_session"],
            },
            "session_jobs": {"used": len(admissions), "limit": limits["max_jobs_per_session"]},
        }


    def _session_usage(self, tables, session_id: str) -> dict[str, Any]:
        row = tables.first("budget_scopes", scope_kind="session", session_id=session_id)
        if row is None:
            return _empty_usage_view()
        return _usage_view(_scope_usage_breakdown(self.ledger, tables, row["budget_scope_id"]))


    def _ancestor_limits(self, tables, parent_job_id: str | None) -> ResourceLimits:
        if not parent_job_id:
            return ResourceLimits()
        chain = _job_scope_chain(self.ledger, tables, parent_job_id)
        cost = _smallest(chain, "max_cost_microusd")
        return ResourceLimits(
            max_total_tokens=_smallest(chain, "max_total_tokens"),
            max_cost_usd=_microusd_text(cost) if cost is not None else None,
            max_runtime_seconds=_smallest(chain, "max_runtime_seconds"),
            idle_timeout_seconds=_smallest(chain, "idle_timeout_seconds"),
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
                self.ledger.open_accounting_reader()
                usage = self._session_usage(self._read(), job.parent_session_id)
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

        with self._write() as tables:
            resolved = self._with_ancestor_limits(
                resolved, self._ancestor_limits(tables, job.parent_job_id),
            )
            effective = resolved.effective_limits()
            existing = self._admission(tables, job.id)
            capacity = self._capacity(tables, job.parent_session_id, resolved)
            usage = self._session_usage(tables, job.parent_session_id)
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
                parent_claim = self._admission(
                    tables, parent_job_id, state=("live", "stopping"),
                    owner_instance_id=owner_instance_id, lease_generation=lease_generation,
                )
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
                tables.tables.ensure_session(fanout_session_id)
                fanout_used = tables.count(
                    "job_admissions", caller_turn_id=caller_turn_id, creates_agent=1,
                    where=lambda row: (
                        row["caller_session_id"]
                        if row["caller_session_id"] is not None
                        else row["session_id"]
                    ) == fanout_session_id,
                )
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
            admitted_seq = 1 + max(
                (row["admitted_seq"] for row in tables.rows("job_admissions")), default=0,
            )
            session_tokens = session_effective["max_total_tokens"]
            session_cost = (
                ResourceLimits.usd_to_microusd(session_effective["max_cost_usd"])
                if session_effective["max_cost_usd"] is not None else None
            )
            tables.insert(
                "budget_scopes",
                {
                    "budget_scope_id": session_scope_id, "scope_kind": "session",
                    "session_id": job.parent_session_id, "job_id": None,
                    "max_total_tokens": session_tokens, "max_cost_microusd": session_cost,
                    "max_runtime_seconds": session_effective["max_runtime_seconds"],
                    "idle_timeout_seconds": session_effective["idle_timeout_seconds"],
                    "created_at": time.time(),
                },
                on_conflict="ignore",
            )
            tables.update(
                "budget_scopes",
                {"max_total_tokens": session_tokens, "max_cost_microusd": session_cost},
                budget_scope_id=session_scope_id,
                where=lambda row: (
                    row["max_total_tokens"], row["max_cost_microusd"],
                ) != (session_tokens, session_cost),
            )
            parent_scope = session_scope_id
            if job.parent_job_id:
                parent = self._admission(tables, job.parent_job_id)
                if parent is not None:
                    parent_scope = parent["budget_scope_id"]
            tables.insert("budget_scopes", {
                "budget_scope_id": scope_id, "scope_kind": "job",
                "session_id": job.parent_session_id, "job_id": job.id,
                "parent_scope_id": parent_scope,
                "max_total_tokens": (
                    effective["max_total_tokens"]
                    if resolved.fields["max_total_tokens"].source == "job"
                    else None
                ),
                "max_cost_microusd": (
                    ResourceLimits.usd_to_microusd(effective["max_cost_usd"])
                    if (
                        effective["max_cost_usd"] is not None
                        and resolved.fields["max_cost_usd"].source == "job"
                    )
                    else None
                ),
                "max_runtime_seconds": effective["max_runtime_seconds"],
                "idle_timeout_seconds": effective["idle_timeout_seconds"],
                "created_at": time.time(),
            })
            tables.insert("job_admissions", {
                "admission_id": admission_id, "job_id": job.id,
                "session_id": job.parent_session_id, "parent_job_id": job.parent_job_id,
                "caller_session_id": caller_session_id or job.caller_session_id,
                "caller_turn_id": caller_turn_id, "creates_agent": int(creates_agent),
                "request_fingerprint": fingerprint, "budget_scope_id": scope_id,
                "dispatch_ready": int(dispatch_ready),
                "borrowed_parent_job_id": borrowed_parent_job_id,
                "state": "preparing", "admitted_seq": admitted_seq, "created_at": time.time(),
            })

        job.admission_id = admission_id
        job.budget_scope_id = scope_id
        job.effective_limits = effective
        job.resolved_limits_snapshot = resolved.to_dict()
        job.status = job.status.__class__.QUEUED
        job.queued_at = job.queued_at or time.time()
        try:
            persist(job)
        except Exception:
            with self._write() as tables:
                tables.delete("job_admissions", admission_id=admission_id, state="preparing")
                tables.delete("budget_scopes", budget_scope_id=scope_id)
            raise
        with self._write() as tables:
            tables.update(
                "job_admissions", {"state": "queued"},
                admission_id=admission_id, state="preparing",
            )
            capacity = self._capacity(tables, job.parent_session_id, resolved)
            usage = self._session_usage(tables, job.parent_session_id)
        return AdmissionDecision(
            accepted=True,
            job_id=job.id,
            reason_code=None,
            retryable=False,
            effective_limits=resolved.to_dict(),
            capacity=capacity,
            usage=usage,
        )


    def _resolved_for_admission(self, tables, job_id: str):
        row = self._admission(tables, job_id)
        if row is None:
            return None, None
        job = Job(
            id=job_id, parent_session_id=row["session_id"], prompt="", agent_id="",
        )
        return row, self._limit_resolver(row["session_id"], job)
