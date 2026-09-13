"""Resource governance: views."""
from __future__ import annotations
import time
from typing import Any
from openprogram.agent.job.types import Job
from openprogram.usage.ledger import UsageLedger
from .contracts import (
    JobResourceView,
)
from .limits import (
    ResolvedLimit,
    ResolvedResourceLimits,
    ResourceLimits,
)
from .usage import (
    _cost_to_microusd,
    _durable_job_time_limits,
    _microusd_text,
    _reason_metadata,
    _scope_usage_breakdown,
)


def _shared_remaining(ledger: UsageLedger, job_id: str) -> dict[str, Any]:
    with ledger.read() as conn:
        scopes = conn.execute(
            """WITH RECURSIVE ancestors AS (
                   SELECT parent.* FROM job_admissions admission
                   JOIN budget_scopes current
                     ON current.budget_scope_id = admission.budget_scope_id
                   JOIN budget_scopes parent
                     ON parent.budget_scope_id = current.parent_scope_id
                   WHERE admission.job_id = ?
                   UNION ALL
                   SELECT parent.* FROM budget_scopes parent
                   JOIN ancestors child
                     ON child.parent_scope_id = parent.budget_scope_id
               )
               SELECT * FROM ancestors""",
            (job_id,),
        ).fetchall()
    token_remaining: list[int] = []
    cost_remaining: list[int] = []
    unknown_cost_events = 0
    for scope in scopes:
        usage = _scope_usage_breakdown(conn, scope["budget_scope_id"])
        unknown_cost_events = max(
            unknown_cost_events, usage["unknown_cost_events"],
        )
        if scope["max_total_tokens"] is not None:
            token_remaining.append(max(
                0,
                int(scope["max_total_tokens"])
                - usage["actual_tokens"]
                - usage["reserved_tokens"],
            ))
        if scope["max_cost_microusd"] is not None:
            cost_remaining.append(max(
                0,
                int(scope["max_cost_microusd"])
                - usage["actual_cost_microusd"]
                - usage["reserved_cost_microusd"],
            ))
    return {
        "tokens": min(token_remaining) if token_remaining else None,
        "cost_usd": (
            None
            if unknown_cost_events or not cost_remaining
            else _microusd_text(min(cost_remaining))
        ),
        "cost_unknown_events": unknown_cost_events,
    }


def build_job_resource_view(
    job: Job,
    *,
    ledger: UsageLedger,
    resolved: ResolvedResourceLimits,
) -> JobResourceView:
    usage = ledger.job_resource_usage(job.id)
    counts = ledger.resource_counts(job.parent_session_id, job.id)
    snapshot = job.resolved_limits_snapshot
    snapshot_applied = False
    if snapshot and isinstance(snapshot, dict) and isinstance(snapshot.get("limits"), dict):
        fields = dict(resolved.fields)
        try:
            for name, value in snapshot["limits"].items():
                if not isinstance(value, dict):
                    continue
                # Time limits are frozen at admission. Parent/job-sourced
                # caps only exist in the snapshot: the resolver drops job
                # inputs after admission, so current config can never
                # reproduce them. Session/global-sourced caps stay live.
                if name in ("max_runtime_seconds", "idle_timeout_seconds") \
                        or value.get("source") in ("parent", "job"):
                    fields[name] = ResolvedLimit(**value)
            resolved = ResolvedResourceLimits(
                scheduler_capacity=resolved.scheduler_capacity, fields=fields,
            )
            snapshot_applied = True
        except (TypeError, ValueError):
            snapshot_applied = False
    if job.admission_id and not snapshot_applied:
        runtime_limit, idle_limit = _durable_job_time_limits(ledger, job.id)
        fields = dict(resolved.fields)
        for name, value in (
            ("max_runtime_seconds", runtime_limit),
            ("idle_timeout_seconds", idle_limit),
        ):
            fields[name] = ResolvedLimit(value, value, "job")
        resolved = ResolvedResourceLimits(
            scheduler_capacity=resolved.scheduler_capacity, fields=fields,
        )
    limits = resolved.effective_limits()
    counts["session_live"]["limit"] = limits["max_live_per_session"]
    counts["session_queued"]["limit"] = limits["max_queued_per_session"]
    counts["session_jobs"]["limit"] = limits["max_jobs_per_session"]
    legacy = not job.admission_id
    has_actual_usage = usage["events"] > 0
    cost_known: bool | None = (
        usage["unknown_cost_events"] == 0
        if has_actual_usage or not legacy else None
    )
    actual_cost = (
        _microusd_text(sum(
            _cost_to_microusd(value) for value in usage["cost_values"]
        ))
        if cost_known is True
        else None
    )
    reason = _reason_metadata(job.reason_code)
    runtime_used: float | None = None
    idle_used: float | None = None
    local_token_limit: int | None = None
    local_cost_limit: str | None = None
    if not legacy:
        with ledger.read() as conn:
            timing = conn.execute(
                """SELECT admission.state, admission.started_at,
                          admission.last_activity_at, admission.released_at,
                          scope.max_total_tokens, scope.max_cost_microusd
                   FROM job_admissions admission
                   JOIN budget_scopes scope
                     ON scope.budget_scope_id = admission.budget_scope_id
                   WHERE admission.job_id = ?""",
                (job.id,),
            ).fetchone()
        if timing is not None:
            local_token_limit = timing["max_total_tokens"]
            if timing["max_cost_microusd"] is not None:
                local_cost_limit = (
                    limits["max_cost_usd"]
                    if resolved.fields["max_cost_usd"].source == "job"
                    else _microusd_text(timing["max_cost_microusd"])
                )
            if timing["started_at"] is not None:
                end = (
                    timing["released_at"]
                    if (
                        timing["state"] == "released"
                        and timing["released_at"] is not None
                    )
                    else time.time()
                )
                runtime_used = max(0.0, end - timing["started_at"])
                activity = timing["last_activity_at"] or timing["started_at"]
                idle_used = max(0.0, end - activity)
    return JobResourceView(
        job_id=job.id,
        status=job.status.value,
        resource_state="legacy/unmetered" if legacy else counts["resource_state"],
        reason_code=job.reason_code,
        reason_key=reason["human_key"],
        retryable=reason["retryable"],
        limits=resolved.to_dict(),
        capacity={
            "scheduler_capacity": resolved.scheduler_capacity,
            "session_live": counts["session_live"],
            "session_queued": counts["session_queued"],
            "session_jobs": counts["session_jobs"],
            "queue_position": counts["queue_position"],
        },
        budget={
            "scope": "legacy/unmetered" if legacy else "job_with_shared_ancestors",
            "tokens": {
                "actual": (
                    usage["total_tokens"] if has_actual_usage or not legacy else None
                ),
                "reserved": None if legacy else counts["reserved_tokens"],
                "limit": local_token_limit,
            },
            "cost_usd": {
                "actual": actual_cost,
                "reserved": (
                    None if legacy else str(ResourceLimits.microusd_to_usd(
                        counts["reserved_cost_microusd"],
                    ))
                ),
                "limit": local_cost_limit,
                "known": cost_known,
                "unknown_events": (
                    usage["unknown_cost_events"]
                    if has_actual_usage or not legacy else None
                ),
            },
            "runtime_seconds": {
                "used": runtime_used,
                "limit": None if legacy else limits["max_runtime_seconds"],
            },
            "idle_seconds": {
                "used": idle_used,
                "limit": None if legacy else limits["idle_timeout_seconds"],
            },
            "shared_remaining": (
                {
                    "tokens": None,
                    "cost_usd": None,
                    "cost_unknown_events": None,
                }
                if legacy else _shared_remaining(ledger, job.id)
            ),
        },
    )

