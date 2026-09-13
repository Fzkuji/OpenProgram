"""Resource governance: limits."""
from __future__ import annotations
import os
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


INTEGER_LIMITS = (
    "max_live_per_session",
    "max_queued_per_session",
    "max_jobs_per_session",
    "max_total_tokens",
    "max_runtime_seconds",
    "idle_timeout_seconds",
)


COST_LIMIT = "max_cost_usd"


LIMIT_FIELDS = (*INTEGER_LIMITS, COST_LIMIT)


JOB_LIMIT_FIELDS = frozenset({
    "max_total_tokens", COST_LIMIT, "max_runtime_seconds", "idle_timeout_seconds",
})


MEANINGFUL_ACTIVITY_KINDS = frozenset({
    "operation_start", "provider_data", "tool_progress", "child_progress", "terminal",
})


SQLITE_INT64_MAX = 9_223_372_036_854_775_807


TERMINAL_FIELD_NAMES = frozenset({
    "status", "head_id", "result_text", "error", "reason_code",
})


_CANONICAL_PROJECTION_OWNER = "canonical-projection"


_RETRYABLE_RESOURCE_REASONS = frozenset({
    "quota.accounting_unavailable",
    "quota.parent_claim_unavailable",
    "quota.queue_full",
    "error.accounting_unavailable",
    "error.worker_lost",
})


_RESOURCE_REASON_CODES = (
    "quota.queue_full",
    "quota.jobs_exhausted",
    "quota.parent_budget_exhausted",
    "quota.parent_claim_unavailable",
    "quota.token_exhausted",
    "quota.cost_exhausted",
    "quota.cost_unavailable",
    "quota.invalid_limits",
    "quota.accounting_unavailable",
    "quota.admission_conflict",
    "quota.spawn_depth",
    "quota.spawn_fanout",
    "cancel.user",
    "cancel.parent",
    "cancel.session",
    "cancel.concurrent",
    "cancel.timeout",
    "budget.token_exhausted",
    "budget.cost_exhausted",
    "budget.runtime_exhausted",
    "budget.idle_exhausted",
    "error.worker_lost",
    "error.accounting_unavailable",
    "error.nonpreemptible_operation",
    "error.operation_timeout",
    "error.execution",
    "error.accepted_side_effect",
    "error.borrowed_cleanup",
    "error.borrowed_parent_lost",
    "error.cancel_token_conflict",
    "error.deferred_inbox_intent_missing",
    "error.dispatch_failed",
    "error.runtime_registration",
    "error.job_missing",
    "error.canonical_unavailable",
    "completed",
)


RESOURCE_REASON_METADATA = {
    code: {
        "retryable": code in _RETRYABLE_RESOURCE_REASONS,
        "human_key": f"resource.reason.{code}",
    }
    for code in _RESOURCE_REASON_CODES
}


class ResourceLimitError(ValueError):
    pass


@dataclass(frozen=True)
class ResourceLimits:
    max_live_per_session: int | None = None
    max_queued_per_session: int | None = None
    max_jobs_per_session: int | None = None
    max_total_tokens: int | None = None
    max_cost_usd: str | None = None
    max_runtime_seconds: int | None = None
    idle_timeout_seconds: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ResourceLimits":
        raw = dict(value or {})
        unknown = raw.keys() - set(LIMIT_FIELDS)
        if unknown:
            raise ResourceLimitError(f"unknown resource limits: {', '.join(sorted(unknown))}")
        clean: dict[str, Any] = {}
        for name in INTEGER_LIMITS:
            item = raw.get(name)
            if item is not None:
                if (isinstance(item, bool) or not isinstance(item, int)
                        or item <= 0 or item > SQLITE_INT64_MAX):
                    raise ResourceLimitError(f"{name} must be a positive integer or null")
            clean[name] = item
        cost = raw.get(COST_LIMIT)
        if cost is not None:
            if not isinstance(cost, str):
                raise ResourceLimitError("max_cost_usd must be a positive decimal string or null")
            try:
                decimal = Decimal(cost)
            except InvalidOperation as exc:
                raise ResourceLimitError(
                    "max_cost_usd must be a positive decimal string or null"
                ) from exc
            if not decimal.is_finite() or decimal <= 0 or decimal.as_tuple().exponent < -6:
                raise ResourceLimitError(
                    "max_cost_usd must be positive with at most 6 decimal places"
                )
            clean[COST_LIMIT] = cost
        else:
            clean[COST_LIMIT] = None
        return cls(**clean)

    def to_dict(self, *, exclude_none: bool = False) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None} if exclude_none else data

    @staticmethod
    def usd_to_microusd(value: str) -> int:
        parsed = ResourceLimits.from_mapping({COST_LIMIT: value}).max_cost_usd
        return int(Decimal(parsed) * 1_000_000)  # type: ignore[arg-type]

    @staticmethod
    def microusd_to_usd(value: int) -> Decimal:
        return Decimal(value) / Decimal(1_000_000)


@dataclass(frozen=True)
class ResolvedLimit:
    configured: int | str | None
    effective: int | str | None
    source: str


@dataclass(frozen=True)
class ResolvedResourceLimits:
    scheduler_capacity: int
    fields: dict[str, ResolvedLimit]

    def effective_limits(self) -> dict[str, int | str | None]:
        return {name: item.effective for name, item in self.fields.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheduler_capacity": self.scheduler_capacity,
            "limits": {name: asdict(value) for name, value in self.fields.items()},
        }


def scheduler_capacity() -> int:
    try:
        return max(1, int(os.environ.get("OPENPROGRAM_JOB_WORKERS") or "4"))
    except ValueError:
        return 4


def _less_or_equal(value: int | str, ceiling: int | str) -> bool:
    if isinstance(value, str) or isinstance(ceiling, str):
        return Decimal(str(value)) <= Decimal(str(ceiling))
    return value <= ceiling


def resolve_resource_limits(
    global_limits: ResourceLimits | Mapping[str, Any],
    *,
    session: ResourceLimits | Mapping[str, Any] | None = None,
    parent: ResourceLimits | Mapping[str, Any] | None = None,
    job: ResourceLimits | Mapping[str, Any] | None = None,
    scheduler_capacity: int | None = None,
) -> ResolvedResourceLimits:
    capacity = scheduler_capacity if scheduler_capacity is not None else globals()["scheduler_capacity"]()
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        raise ResourceLimitError("scheduler capacity must be a positive integer")
    levels = [
        ("global", ResourceLimits.from_mapping(
            global_limits.to_dict() if isinstance(global_limits, ResourceLimits) else global_limits
        )),
        ("session", ResourceLimits.from_mapping(
            session.to_dict() if isinstance(session, ResourceLimits) else session
        )),
        ("parent", ResourceLimits.from_mapping(
            parent.to_dict() if isinstance(parent, ResourceLimits) else parent
        )),
        ("job", ResourceLimits.from_mapping(
            job.to_dict() if isinstance(job, ResourceLimits) else job
        )),
    ]
    job_values = levels[-1][1]
    forbidden = [
        name for name in LIMIT_FIELDS
        if name not in JOB_LIMIT_FIELDS and getattr(job_values, name) is not None
    ]
    if forbidden:
        raise ResourceLimitError("job limits cannot set session capacity")

    fields: dict[str, ResolvedLimit] = {}
    for name in LIMIT_FIELDS:
        configured: int | str | None = None
        source = "unlimited"
        for level, limits in levels:
            candidate = getattr(limits, name)
            if candidate is None:
                continue
            if configured is not None and not _less_or_equal(candidate, configured):
                raise ResourceLimitError(f"{level} {name} cannot widen {source} limit")
            configured = candidate
            source = level
        effective = configured
        effective_source = source
        if name == "max_live_per_session":
            if effective is None or int(effective) > capacity:
                effective = capacity
                effective_source = "scheduler_capacity"
        fields[name] = ResolvedLimit(configured, effective, effective_source)
    return ResolvedResourceLimits(capacity, fields)


def global_resource_limits() -> ResourceLimits:
    from openprogram import setup

    cfg = setup._read_config()
    raw = ((cfg.get("agent") or {}).get("resource_limits") or {})
    return ResourceLimits.from_mapping(raw)


def session_resource_limits(session_id: str) -> ResourceLimits:
    from openprogram.agent.session_db import default_db

    row = default_db().get_session(session_id) or {}
    return ResourceLimits.from_mapping(row.get("resource_limits") or {})


def save_session_resource_limits(
    session_id: str,
    limits: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
) -> ResourceLimits:
    from openprogram.agent.authority import normalize_authority, owner_principal_id

    normalized = normalize_authority(authority)
    if (
        normalized.get("speaker_kind") != "owner"
        or normalized.get("authority_tier") != "owner"
        or normalized.get("interaction") != "interactive"
        or normalized.get("principal_id") != owner_principal_id()
    ):
        raise PermissionError("only the local interactive owner may change resource limits")
    parsed = ResourceLimits.from_mapping(limits)
    global_limits = global_resource_limits()
    resolve_resource_limits(global_limits, session=parsed)
    from openprogram.agent.session_db import default_db

    db = default_db()
    if db.get_session(session_id) is None:
        raise KeyError(session_id)
    db.update_session(session_id, resource_limits=parsed.to_dict(exclude_none=True))
    return parsed

