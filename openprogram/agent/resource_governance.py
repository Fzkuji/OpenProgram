"""Resource-limit parsing, inheritance, and read-only task diagnostics."""
from __future__ import annotations

import os
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Callable, Mapping

from openprogram.agent.task.types import Task, TaskStatus, is_terminal
from openprogram.usage.ledger import UsageLedger


INTEGER_LIMITS = (
    "max_live_per_session",
    "max_queued_per_session",
    "max_tasks_per_session",
    "max_total_tokens",
    "max_runtime_seconds",
    "idle_timeout_seconds",
)
COST_LIMIT = "max_cost_usd"
LIMIT_FIELDS = (*INTEGER_LIMITS, COST_LIMIT)
TASK_LIMIT_FIELDS = frozenset({
    "max_total_tokens", COST_LIMIT, "max_runtime_seconds", "idle_timeout_seconds",
})
MEANINGFUL_ACTIVITY_KINDS = frozenset({
    "operation_start", "provider_data", "tool_progress", "child_progress", "terminal",
})
SQLITE_INT64_MAX = 9_223_372_036_854_775_807
TERMINAL_FIELD_NAMES = frozenset({
    "status", "head_id", "result_text", "error", "reason_code",
})
_RETRYABLE_RESOURCE_REASONS = frozenset({
    "quota.accounting_unavailable",
    "quota.parent_claim_unavailable",
    "quota.queue_full",
    "error.accounting_unavailable",
    "error.worker_lost",
})
_RESOURCE_REASON_CODES = (
    "quota.queue_full",
    "quota.tasks_exhausted",
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
    "error.task_missing",
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
    max_tasks_per_session: int | None = None
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
        return max(1, int(os.environ.get("OPENPROGRAM_TASK_WORKERS") or "4"))
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
    task: ResourceLimits | Mapping[str, Any] | None = None,
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
        ("task", ResourceLimits.from_mapping(
            task.to_dict() if isinstance(task, ResourceLimits) else task
        )),
    ]
    task_values = levels[-1][1]
    forbidden = [
        name for name in LIMIT_FIELDS
        if name not in TASK_LIMIT_FIELDS and getattr(task_values, name) is not None
    ]
    if forbidden:
        raise ResourceLimitError("task limits cannot set session capacity")

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


@dataclass(frozen=True)
class TaskResourceView:
    task_id: str
    status: str
    resource_state: str
    reason_code: str | None
    reason_key: str | None
    retryable: bool
    limits: dict[str, Any]
    capacity: dict[str, Any]
    budget: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    task_id: str | None
    reason_code: str | None
    retryable: bool
    effective_limits: dict[str, Any]
    capacity: dict[str, Any]
    idempotent: bool = False
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdmissionRejected(RuntimeError):
    def __init__(self, decision: AdmissionDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason_code or "resource admission rejected")

    def to_dict(self) -> dict[str, Any]:
        return self.decision.to_dict()


@dataclass(frozen=True)
class ReservationDecision:
    accepted: bool
    reservation_id: str | None
    reason_code: str | None
    retryable: bool


@dataclass(frozen=True)
class RequestReservation:
    """Conservative bounds and durable identity for one provider request."""

    allowed: bool
    reason_code: str | None
    input_token_upper_bound: int
    output_token_cap: int
    token_reservation: int
    cost_known: bool
    cost_reservation_microusd: int | None
    reservation_id: str | None = None


def plan_request_reservation(
    *,
    input_token_upper_bound: int,
    requested_max_output_tokens: int,
    remaining_token_budget: int | None,
    model: Any,
    cost_budget_configured: bool = False,
) -> RequestReservation:
    """Return safe request bounds without contacting a provider or ledger.

    B5 consumes this DTO to perform the atomic reserve/start/settle sequence.
    """
    for name, value in (
        ("input_token_upper_bound", input_token_upper_bound),
        ("requested_max_output_tokens", requested_max_output_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if remaining_token_budget is not None and (
        isinstance(remaining_token_budget, bool)
        or not isinstance(remaining_token_budget, int)
        or remaining_token_budget < 0
    ):
        raise ValueError("remaining_token_budget must be a non-negative integer or None")
    model_cap = getattr(model, "max_tokens", None)
    if not isinstance(model_cap, int) or model_cap <= 0:
        model_cap = requested_max_output_tokens
    output_cap = min(requested_max_output_tokens, model_cap)
    if remaining_token_budget is not None:
        available_output = max(0, remaining_token_budget - input_token_upper_bound)
        output_cap = min(output_cap, available_output)
        if input_token_upper_bound > remaining_token_budget:
            return RequestReservation(
                False, "quota.token_exhausted", input_token_upper_bound, 0,
                input_token_upper_bound, False, None,
            )
    token_reservation = input_token_upper_bound + output_cap
    if token_reservation > 9_223_372_036_854_775_807:
        return RequestReservation(False, "quota.accounting_unavailable", input_token_upper_bound,
                                  output_cap, token_reservation, False, None)
    if output_cap == 0:
        return RequestReservation(False, "quota.token_exhausted", input_token_upper_bound, 0,
                                  token_reservation, False, None)
    pricing = getattr(model, "cost", None)
    price_known = pricing is not None and bool(getattr(pricing, "is_known", lambda: False)())
    if cost_budget_configured and not price_known:
        return RequestReservation(
            False, "quota.cost_unavailable", input_token_upper_bound, output_cap,
            token_reservation, False, None,
        )
    estimated_cost = None
    if price_known:
        estimated_cost = int((
            Decimal(input_token_upper_bound) * (Decimal(str(pricing.input))
                                                 + Decimal(str(pricing.cache_read))
                                                 + Decimal(str(pricing.cache_write)))
            + Decimal(output_cap) * Decimal(str(pricing.output))
        ).to_integral_value(rounding=ROUND_CEILING))
        if estimated_cost > 9_223_372_036_854_775_807:
            return RequestReservation(False, "quota.accounting_unavailable", input_token_upper_bound,
                                      output_cap, token_reservation, True, None)
    return RequestReservation(
        True, None, input_token_upper_bound, output_cap, token_reservation,
        price_known, estimated_cost,
    )


@dataclass(frozen=True)
class DispatchClaim:
    task_id: str
    session_id: str
    lease_generation: int


@dataclass(frozen=True)
class ReconcileResult:
    finalized_preparing: int = 0
    rolled_back_preparing: int = 0
    released_missing: int = 0
    released_worker_lost: int = 0
    finalization_conflicts: int = 0
    completed_pending: tuple[tuple[str, str], ...] = ()


def _durable_task_time_limits(
    ledger: UsageLedger, task_id: str,
) -> tuple[int | None, int | None]:
    row = ledger.connection().execute(
        """WITH RECURSIVE ancestors AS (
               SELECT b.* FROM task_admissions a
               JOIN budget_scopes b ON b.budget_scope_id = a.budget_scope_id
               WHERE a.task_id = ?
               UNION ALL
               SELECT parent.* FROM budget_scopes parent
               JOIN ancestors child
                 ON child.parent_scope_id = parent.budget_scope_id
           )
           SELECT MIN(max_runtime_seconds), MIN(idle_timeout_seconds)
           FROM ancestors WHERE scope_kind = 'task'""",
        (task_id,),
    ).fetchone()
    return (None, None) if row is None else (row[0], row[1])


def _task_fingerprint(task: Task) -> str:
    facts = {
        name: getattr(task, name)
        for name in (
            "id", "parent_session_id", "prompt", "agent_id", "context_mode",
            "parent_msg_id", "parent_task_id", "caller_msg_id", "caller_session_id",
            "chain_messages", "chain_generations", "caller_chain_generations",
            "worktree_id", "wait", "archive_when_done", "spawn_caller",
            "advance_head", "tools_override", "deferred_inbox",
        )
    }
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reason_metadata(reason_code: str | None) -> dict[str, Any]:
    if reason_code is None:
        return {"retryable": False, "human_key": None}
    return RESOURCE_REASON_METADATA.get(reason_code, {
        "retryable": False,
        "human_key": "resource.reason.unknown",
    })


def _cost_to_microusd(value: Any) -> int:
    return int(
        (Decimal(str(value)) * Decimal(1_000_000)).to_integral_value(
            rounding=ROUND_HALF_UP,
        )
    )


def _microusd_text(value: int) -> str:
    return format(ResourceLimits.microusd_to_usd(value), ".6f")


def _scope_usage_breakdown(conn, scope_id: str) -> dict[str, int]:
    events = conn.execute(
        """WITH RECURSIVE descendants(id) AS (
                SELECT ? UNION ALL
                SELECT b.budget_scope_id FROM budget_scopes b
                JOIN descendants d ON b.parent_scope_id = d.id
            )
            SELECT total_tokens, cost_total, cost_source
            FROM usage_events
            WHERE budget_scope_id IN (SELECT id FROM descendants)""",
        (scope_id,),
    ).fetchall()
    reserved = conn.execute(
        """WITH RECURSIVE descendants(id) AS (
                SELECT ? UNION ALL
                SELECT b.budget_scope_id FROM budget_scopes b
                JOIN descendants d ON b.parent_scope_id = d.id
            )
            SELECT COALESCE(SUM(reserved_tokens), 0),
                   COALESCE(SUM(reserved_cost_microusd), 0)
            FROM usage_reservations
            WHERE budget_scope_id IN (SELECT id FROM descendants)
              AND state IN ('reserved','started')""",
        (scope_id,),
    ).fetchone()
    return {
        "actual_tokens": sum(int(row["total_tokens"] or 0) for row in events),
        "actual_cost_microusd": sum(
            _cost_to_microusd(row["cost_total"] or 0) for row in events
        ),
        "unknown_cost_events": sum(
            (row["cost_source"] or "unknown") == "unknown" for row in events
        ),
        "reserved_tokens": int(reserved[0] or 0),
        "reserved_cost_microusd": int(reserved[1] or 0),
    }


def _usage_view(usage: Mapping[str, int]) -> dict[str, Any]:
    unknown = usage["unknown_cost_events"]
    return {
        "tokens": {
            "actual": usage["actual_tokens"],
            "reserved": usage["reserved_tokens"],
        },
        "cost_usd": {
            "actual": (
                None if unknown else _microusd_text(usage["actual_cost_microusd"])
            ),
            "reserved": _microusd_text(usage["reserved_cost_microusd"]),
            "known": unknown == 0,
            "unknown_events": unknown,
        },
    }


def _empty_usage_view() -> dict[str, Any]:
    return _usage_view({
        "actual_tokens": 0,
        "actual_cost_microusd": 0,
        "unknown_cost_events": 0,
        "reserved_tokens": 0,
        "reserved_cost_microusd": 0,
    })


class ResourceGovernor:
    """Single durable admission boundary backed by the usage SQLite DB."""

    def __init__(
        self,
        ledger: UsageLedger,
        *,
        limit_resolver: Callable[[str, Task], ResolvedResourceLimits] | None = None,
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
                Task(id="", parent_session_id=session_id, prompt="", agent_id=""),
            )

    @staticmethod
    def _resolve_limits(session_id: str, task: Task) -> ResolvedResourceLimits:
        # After admission this field is the resolved snapshot persisted on
        # Task, not a new child-limit request. Replaying that snapshot as
        # input would treat session-only capacity as an illegal task limit
        # and break idempotent dispatch/resume.
        task_limits = ResourceLimits.from_mapping(
            {} if task.admission_id else (task.effective_limits or {})
        )
        return resolve_resource_limits(
            global_resource_limits(), session=session_resource_limits(session_id),
            task=task_limits,
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
               FROM task_admissions WHERE session_id = ?""",
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
            "session_tasks": {"used": int(row[2] or 0), "limit": limits["max_tasks_per_session"]},
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
            task_id=None,
            reason_code=reason_code,
            retryable=retryable,
            effective_limits=resolved.to_dict(),
            capacity=capacity,
            usage=usage or _empty_usage_view(),
        )

    def admit_task(
        self,
        task: Task,
        *,
        persist: Callable[[Task], Any],
        creates_agent: bool = True,
        caller_session_id: str | None = None,
        caller_turn_id: str | None = None,
        dispatch_ready: bool = True,
        borrowed_claim: tuple[str, str, int] | None = None,
    ) -> AdmissionDecision:
        try:
            resolved = self._limit_resolver(task.parent_session_id, task)
        except ResourceLimitError:
            fallback = resolve_resource_limits(ResourceLimits())
            try:
                usage = self._session_usage(
                    self.ledger.connection(), task.parent_session_id,
                )
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
        fingerprint = _task_fingerprint(task)
        admission_id = "adm_" + uuid.uuid4().hex
        scope_id = "budget_" + uuid.uuid4().hex
        session_scope_id = "session_" + hashlib.sha256(
            task.parent_session_id.encode("utf-8")
        ).hexdigest()[:24]
        effective = resolved.effective_limits()
        session_effective = self._session_limit_resolver(
            task.parent_session_id
        ).effective_limits()
        spawn_depth_limit = spawn_fanout_limit = 0
        if creates_agent:
            from openprogram.functions.tools.agent.agent.agent import (
                max_spawn_depth,
                max_spawn_fanout,
            )
            spawn_depth_limit = max_spawn_depth()
            spawn_fanout_limit = max_spawn_fanout()

        with self.ledger.immediate() as conn:
            existing = conn.execute(
                """SELECT admission_id, request_fingerprint, budget_scope_id, state
                   FROM task_admissions WHERE task_id = ?""",
                (task.id,),
            ).fetchone()
            capacity = self._capacity(conn, task.parent_session_id, resolved)
            usage = self._session_usage(conn, task.parent_session_id)
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    return self._denied(
                        "quota.admission_conflict", resolved=resolved,
                        capacity=capacity, retryable=False, usage=usage,
                    )
                task.admission_id = existing["admission_id"]
                task.budget_scope_id = existing["budget_scope_id"]
                task.effective_limits = effective
                task.resolved_limits_snapshot = resolved.to_dict()
                return AdmissionDecision(
                    accepted=True,
                    task_id=task.id,
                    reason_code=None,
                    retryable=False,
                    effective_limits=resolved.to_dict(),
                    capacity=capacity,
                    idempotent=True,
                    usage=usage,
                )
            borrowed_parent_task_id = None
            if borrowed_claim is not None:
                parent_task_id, owner_instance_id, lease_generation = borrowed_claim
                parent_claim = conn.execute(
                    """SELECT session_id FROM task_admissions
                       WHERE task_id = ? AND state IN ('live','stopping')
                         AND owner_instance_id = ? AND lease_generation = ?""",
                    (parent_task_id, owner_instance_id, lease_generation),
                ).fetchone()
                if (
                    parent_claim is None
                    or parent_claim["session_id"] != task.parent_session_id
                ):
                    return self._denied(
                        "quota.parent_claim_unavailable",
                        resolved=resolved,
                        capacity=capacity,
                        retryable=True,
                        usage=usage,
                    )
                borrowed_parent_task_id = parent_task_id
            if (
                spawn_depth_limit
                and task.chain_generations > spawn_depth_limit
            ):
                return self._denied(
                    "quota.spawn_depth", resolved=resolved,
                    capacity=capacity, retryable=False, usage=usage,
                )
            if creates_agent and caller_turn_id and spawn_fanout_limit:
                fanout_session_id = (
                    caller_session_id
                    or task.caller_session_id
                    or task.parent_session_id
                )
                fanout_used = conn.execute(
                    """SELECT COUNT(*) FROM task_admissions
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
            cumulative = capacity["session_tasks"]
            if cumulative["limit"] is not None and cumulative["used"] >= cumulative["limit"]:
                return self._denied(
                    "quota.tasks_exhausted", resolved=resolved,
                    capacity=capacity, retryable=False, usage=usage,
                )
            admitted_seq = conn.execute(
                "SELECT COALESCE(MAX(admitted_seq), 0) + 1 FROM task_admissions"
            ).fetchone()[0]
            conn.execute(
                """INSERT OR IGNORE INTO budget_scopes (
                    budget_scope_id, scope_kind, session_id, task_id,
                    max_total_tokens, max_cost_microusd,
                    max_runtime_seconds, idle_timeout_seconds, created_at
                ) VALUES (?, 'session', ?, NULL, ?, ?, ?, ?, ?)""",
                (
                    session_scope_id, task.parent_session_id,
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
            if task.parent_task_id:
                parent = conn.execute(
                    "SELECT budget_scope_id FROM task_admissions WHERE task_id = ?",
                    (task.parent_task_id,),
                ).fetchone()
                if parent is not None:
                    parent_scope = parent[0]
            conn.execute(
                """INSERT INTO budget_scopes (
                    budget_scope_id, scope_kind, session_id, task_id,
                    parent_scope_id, max_total_tokens, max_cost_microusd,
                    max_runtime_seconds, idle_timeout_seconds, created_at
                ) VALUES (?, 'task', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope_id, task.parent_session_id, task.id, parent_scope,
                    effective["max_total_tokens"]
                    if resolved.fields["max_total_tokens"].source == "task"
                    else None,
                    ResourceLimits.usd_to_microusd(effective["max_cost_usd"])
                    if (
                        effective["max_cost_usd"] is not None
                        and resolved.fields["max_cost_usd"].source == "task"
                    )
                    else None,
                    effective["max_runtime_seconds"], effective["idle_timeout_seconds"],
                    time.time(),
                ),
            )
            conn.execute(
                """INSERT INTO task_admissions (
                    admission_id, task_id, session_id, parent_task_id,
                    caller_session_id, caller_turn_id, creates_agent,
                    request_fingerprint,
                    budget_scope_id, dispatch_ready, borrowed_parent_task_id, state,
                    admitted_seq, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preparing', ?, ?)""",
                (
                    admission_id, task.id, task.parent_session_id, task.parent_task_id,
                    caller_session_id or task.caller_session_id,
                    caller_turn_id, int(creates_agent), fingerprint, scope_id,
                    int(dispatch_ready), borrowed_parent_task_id,
                    admitted_seq, time.time(),
                ),
            )

        task.admission_id = admission_id
        task.budget_scope_id = scope_id
        task.effective_limits = effective
        task.resolved_limits_snapshot = resolved.to_dict()
        task.status = task.status.__class__.QUEUED
        task.queued_at = task.queued_at or time.time()
        try:
            persist(task)
        except Exception:
            with self.ledger.immediate() as conn:
                conn.execute(
                    "DELETE FROM task_admissions WHERE admission_id = ? AND state = 'preparing'",
                    (admission_id,),
                )
                conn.execute("DELETE FROM budget_scopes WHERE budget_scope_id = ?", (scope_id,))
            raise
        with self.ledger.immediate() as conn:
            conn.execute(
                "UPDATE task_admissions SET state = 'queued' "
                "WHERE admission_id = ? AND state = 'preparing'",
                (admission_id,),
            )
            capacity = self._capacity(conn, task.parent_session_id, resolved)
            usage = self._session_usage(conn, task.parent_session_id)
        return AdmissionDecision(
            accepted=True,
            task_id=task.id,
            reason_code=None,
            retryable=False,
            effective_limits=resolved.to_dict(),
            capacity=capacity,
            usage=usage,
        )

    def _resolved_for_admission(self, conn, task_id: str):
        row = conn.execute(
            "SELECT session_id FROM task_admissions WHERE task_id = ?", (task_id,),
        ).fetchone()
        if row is None:
            return None, None
        task = Task(
            id=task_id, parent_session_id=row["session_id"], prompt="", agent_id="",
        )
        return row, self._limit_resolver(row["session_id"], task)

    @staticmethod
    def _owner_holds_worker_lock(owner_instance_id: str) -> bool:
        """Validate that this caller owns the profile's singleton worker lock."""
        owner_pid = os.getpid()
        if owner_instance_id.startswith("worker_"):
            try:
                owner_pid = int(owner_instance_id.split("_", 2)[1])
            except (IndexError, ValueError):
                return False
            if owner_pid != os.getpid():
                return False
        try:
            from openprogram.worker.lock import is_held_by
            return is_held_by(owner_pid)
        except Exception:
            return False

    def try_start(self, task_id: str, *, owner_instance_id: str) -> bool:
        """Atomically exchange queued capacity for live capacity."""
        if not self._owner_holds_worker_lock(owner_instance_id):
            return False
        with self.ledger.immediate() as conn:
            row, resolved = self._resolved_for_admission(conn, task_id)
            if row is None or resolved is None:
                return False
            admission = conn.execute(
                """SELECT state, owner_instance_id FROM task_admissions
                   WHERE task_id = ?""", (task_id,),
            ).fetchone()
            if admission["state"] == "live":
                return admission["owner_instance_id"] == owner_instance_id
            if admission["state"] != "queued":
                return False
            capacity = self._capacity(conn, row["session_id"], resolved)
            live = capacity["session_live"]
            global_live = conn.execute(
                "SELECT COUNT(*) FROM task_admissions WHERE state IN ('live','stopping')"
            ).fetchone()[0]
            if global_live >= resolved.scheduler_capacity:
                return False
            if live["limit"] is not None and live["used"] >= live["limit"]:
                return False
            now = time.time()
            changed = conn.execute(
                """UPDATE task_admissions
                   SET state = 'live', owner_instance_id = ?, started_at = ?,
                       last_activity_at = ?, lease_expires_at = ?,
                       lease_generation = lease_generation + 1
                   WHERE task_id = ? AND state = 'queued'
                     AND dispatch_ready = 1""",
                (owner_instance_id, now, now, now + 30.0, task_id),
            ).rowcount
            return changed == 1

    def claim_next(
        self,
        *,
        owner_instance_id: str,
        excluded_sessions: set[str] | None = None,
        only_task_id: str | None = None,
    ) -> DispatchClaim | None:
        """Claim the globally oldest queued task whose session is eligible."""
        if not self._owner_holds_worker_lock(owner_instance_id):
            return None
        excluded_sessions = excluded_sessions or set()
        with self.ledger.immediate() as conn:
            queued = conn.execute(
                """SELECT task_id, session_id FROM task_admissions
                   WHERE state = 'queued' AND dispatch_ready = 1
                   ORDER BY admitted_seq"""
            ).fetchall()
            for candidate in queued:
                if only_task_id is not None and candidate["task_id"] != only_task_id:
                    continue
                if candidate["session_id"] in excluded_sessions:
                    continue
                row, resolved = self._resolved_for_admission(
                    conn, candidate["task_id"],
                )
                if row is None or resolved is None:
                    continue
                global_live = conn.execute(
                    """SELECT COUNT(*) FROM task_admissions
                       WHERE state IN ('live','stopping')"""
                ).fetchone()[0]
                if global_live >= resolved.scheduler_capacity:
                    return None
                capacity = self._capacity(conn, candidate["session_id"], resolved)
                live = capacity["session_live"]
                if live["limit"] is not None and live["used"] >= live["limit"]:
                    continue
                now = time.time()
                changed = conn.execute(
                    """UPDATE task_admissions
                       SET state = 'live', owner_instance_id = ?, started_at = ?,
                           last_activity_at = ?, lease_expires_at = ?,
                           lease_generation = lease_generation + 1
                       WHERE task_id = ? AND state = 'queued'""",
                    (
                        owner_instance_id, now, now, now + 30.0,
                        candidate["task_id"],
                    ),
                ).rowcount
                if changed == 1:
                    generation = conn.execute(
                        "SELECT lease_generation FROM task_admissions WHERE task_id = ?",
                        (candidate["task_id"],),
                    ).fetchone()[0]
                    return DispatchClaim(
                        candidate["task_id"], candidate["session_id"], generation,
                    )
            return None

    def stage_deferred_resume(
        self,
        task_id: str,
        *,
        admission_id: str,
        parent_msg_id: str,
    ) -> bool:
        """Persist the mutable target head under the original admission fence."""
        with self.ledger.immediate() as conn:
            row = conn.execute(
                """SELECT resume_parent_msg_id FROM task_admissions
                   WHERE task_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0
                     AND borrowed_parent_task_id IS NULL""",
                (task_id, admission_id),
            ).fetchone()
            if row is None:
                return False
            staged = row["resume_parent_msg_id"]
            if staged is not None and staged != parent_msg_id:
                return False
            conn.execute(
                """UPDATE task_admissions SET resume_parent_msg_id = ?
                   WHERE task_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0""",
                (parent_msg_id, task_id, admission_id),
            )
            return True

    def mark_dispatch_ready(
        self,
        task_id: str,
        *,
        admission_id: str,
        parent_msg_id: str,
    ) -> bool:
        """Publish a staged deferred Task under its immutable admission fence."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE task_admissions
                   SET dispatch_ready = 1, resume_parent_msg_id = NULL
                   WHERE task_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0
                     AND resume_parent_msg_id = ?
                     AND borrowed_parent_task_id IS NULL""",
                (task_id, admission_id, parent_msg_id),
            ).rowcount == 1

    def reset_deferred_resume(
        self,
        task_id: str,
        *,
        admission_id: str,
        parent_msg_id: str,
    ) -> bool:
        """Undo a staged head whose TaskStore update did not survive."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE task_admissions SET resume_parent_msg_id = NULL
                   WHERE task_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0
                     AND resume_parent_msg_id = ?""",
                (task_id, admission_id, parent_msg_id),
            ).rowcount == 1

    def pending_deferred_resumes(self) -> list[tuple[str, str, str, str]]:
        """Return staged resume publications left incomplete by a crash."""
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """SELECT task_id, session_id, admission_id, resume_parent_msg_id
                   FROM task_admissions
                   WHERE state = 'queued' AND dispatch_ready = 0
                     AND resume_parent_msg_id IS NOT NULL
                     AND borrowed_parent_task_id IS NULL
                   ORDER BY admitted_seq"""
            ).fetchall()
        return [
            (
                str(row["task_id"]), str(row["session_id"]),
                str(row["admission_id"]), str(row["resume_parent_msg_id"]),
            )
            for row in rows
        ]

    def deferred_dispatches(self) -> list[tuple[str, str]]:
        """Return admitted Tasks waiting for their inbox intent."""
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """SELECT task_id, session_id FROM task_admissions
                   WHERE state = 'queued' AND dispatch_ready = 0
                     AND borrowed_parent_task_id IS NULL
                     AND resume_parent_msg_id IS NULL
                   ORDER BY admitted_seq"""
            ).fetchall()
        return [(str(row["task_id"]), str(row["session_id"])) for row in rows]

    def release_borrowed_task(
        self,
        task_id: str,
        *,
        parent_task_id: str,
        owner_instance_id: str,
        lease_generation: int,
        reason_code: str,
    ) -> bool:
        """Release a child only while its borrowed parent fence is current."""
        with self.ledger.immediate() as conn:
            parent = conn.execute(
                """SELECT 1 FROM task_admissions
                   WHERE task_id = ? AND state IN ('live','stopping')
                     AND owner_instance_id = ? AND lease_generation = ?""",
                (parent_task_id, owner_instance_id, lease_generation),
            ).fetchone()
            if parent is None:
                return False
            return conn.execute(
                """UPDATE task_admissions
                   SET state = 'released', released_at = ?, reason_code = ?,
                       lease_expires_at = NULL
                   WHERE task_id = ? AND state = 'queued'
                     AND borrowed_parent_task_id = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM task_finalizations
                         WHERE task_finalizations.task_id = task_admissions.task_id
                           AND task_finalizations.state = 'pending'
                     )""",
                (time.time(), reason_code, task_id, parent_task_id),
            ).rowcount == 1

    def start_borrowed_task(
        self,
        task_id: str,
        *,
        parent_task_id: str,
        owner_instance_id: str,
        lease_generation: int,
    ) -> bool:
        """Fence a borrowed child's runtime without consuming live capacity."""
        with self.ledger.immediate() as conn:
            parent = conn.execute(
                """SELECT 1 FROM task_admissions
                   WHERE task_id = ? AND state IN ('live','stopping')
                     AND owner_instance_id = ? AND lease_generation = ?""",
                (parent_task_id, owner_instance_id, lease_generation),
            ).fetchone()
            if parent is None:
                return False
            now = time.time()
            return conn.execute(
                """UPDATE task_admissions
                   SET owner_instance_id = ?, lease_generation = ?,
                       started_at = ?, last_activity_at = ?, lease_expires_at = ?
                   WHERE task_id = ? AND state = 'queued'
                     AND dispatch_ready = 0
                     AND borrowed_parent_task_id = ?
                     AND owner_instance_id IS NULL""",
                (
                    owner_instance_id, lease_generation, now, now, now + 30.0,
                    task_id, parent_task_id,
                ),
            ).rowcount == 1

    def renew_borrowed_lease(
        self,
        task_id: str,
        *,
        parent_task_id: str,
        owner_instance_id: str,
        lease_generation: int,
    ) -> bool:
        """Renew the child entry only while the borrowed parent fence is live."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE task_admissions AS child SET lease_expires_at = ?
                   WHERE child.task_id = ? AND child.state = 'queued'
                     AND child.borrowed_parent_task_id = ?
                     AND child.owner_instance_id = ?
                     AND child.lease_generation = ?
                     AND EXISTS (
                         SELECT 1 FROM task_admissions AS parent
                         WHERE parent.task_id = child.borrowed_parent_task_id
                           AND parent.state IN ('live','stopping')
                           AND parent.owner_instance_id = ?
                           AND parent.lease_generation = ?
                     )""",
                (
                    time.time() + 30.0, task_id, parent_task_id,
                    owner_instance_id, lease_generation,
                    owner_instance_id, lease_generation,
                ),
            ).rowcount == 1

    def finalize_borrowed_task(
        self,
        task_id: str,
        *,
        parent_task_id: str,
        owner_instance_id: str,
        lease_generation: int,
        reason_code: str,
        terminal_fields: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """Finalize a borrowed child under its exact parent fence."""
        if terminal_fields.get("reason_code") != reason_code:
            raise ValueError("terminal_fields reason_code must match reason_code")
        return self._finalize_with_intent(
            task_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=("queued",),
            borrowed_parent_task_id=parent_task_id,
            require_parent_fence=True,
            mutate=mutate,
        )

    def release_orphaned_borrowed_tasks(self) -> list[tuple[str, str]]:
        """Release borrowed children whose parent no longer owns a claim."""
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """SELECT child.task_id, child.session_id
                   FROM task_admissions AS child
                   LEFT JOIN task_admissions AS parent
                     ON parent.task_id = child.borrowed_parent_task_id
                   WHERE child.state = 'queued'
                     AND child.borrowed_parent_task_id IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM task_finalizations
                         WHERE task_finalizations.task_id = child.task_id
                           AND task_finalizations.state = 'pending'
                     )
                     AND (parent.task_id IS NULL
                          OR parent.state NOT IN ('live','stopping'))"""
            ).fetchall()
            for row in rows:
                conn.execute(
                    """UPDATE task_admissions
                       SET state = 'released', released_at = ?,
                           reason_code = 'error.borrowed_parent_lost'
                       WHERE task_id = ? AND state = 'queued'
                         AND NOT EXISTS (
                             SELECT 1 FROM task_finalizations
                             WHERE task_finalizations.task_id = task_admissions.task_id
                               AND task_finalizations.state = 'pending'
                         )""",
                    (time.time(), row["task_id"]),
                )
        return [(str(row["task_id"]), str(row["session_id"])) for row in rows]

    def renew_lease(
        self, task_id: str, *, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        with self.ledger.immediate() as conn:
            now = time.time()
            return conn.execute(
                """UPDATE task_admissions SET lease_expires_at = ?
                   WHERE task_id = ? AND owner_instance_id = ?
                     AND lease_generation = ?
                     AND state IN ('live','stopping')""",
                (now + 30.0, task_id, owner_instance_id, lease_generation),
            ).rowcount == 1

    def requeue_task(
        self, task_id: str, *, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        """Return a claimed task to queued when another turn owns its session."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE task_admissions
                   SET state = 'queued', owner_instance_id = NULL,
                       lease_expires_at = NULL, started_at = NULL,
                       last_activity_at = NULL
                   WHERE task_id = ? AND state = 'live'
                     AND owner_instance_id = ? AND lease_generation = ?""",
                (task_id, owner_instance_id, lease_generation),
            ).rowcount == 1

    def finalize_stopping_task(
        self,
        task_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        reason_code: str,
        terminal_fields: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """Finalize this exact stopping claim."""
        if terminal_fields.get("reason_code") != reason_code:
            raise ValueError("terminal_fields reason_code must match reason_code")
        return self._finalize_with_intent(
            task_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=("stopping",),
            use_admission_reason=True,
            mutate=mutate,
        )

    def abandon_stopping_task(
        self, task_id: str, *, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        """Revoke a failed stopping claim so reconciliation can finish it."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE task_admissions
                   SET owner_instance_id = NULL,
                       lease_generation = lease_generation + 1,
                       lease_expires_at = NULL
                   WHERE task_id = ? AND state = 'stopping'
                     AND owner_instance_id = ? AND lease_generation = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM task_finalizations
                         WHERE task_finalizations.task_id = task_admissions.task_id
                           AND task_finalizations.state = 'pending'
                     )""",
                (task_id, owner_instance_id, lease_generation),
            ).rowcount == 1

    def request_stop(self, task_id: str, reason_code: str) -> None:
        with self.ledger.immediate() as conn:
            conn.execute(
                """UPDATE task_admissions
                   SET state = CASE
                       WHEN state = 'live' THEN 'stopping'
                       WHEN state = 'queued'
                            AND borrowed_parent_task_id IS NOT NULL
                            AND owner_instance_id IS NOT NULL THEN state
                       WHEN state IN ('preparing','queued') THEN 'released'
                       ELSE state END,
                       reason_code = ?,
                       released_at = CASE
                           WHEN state IN ('preparing','queued')
                                AND NOT (
                                    state = 'queued'
                                    AND borrowed_parent_task_id IS NOT NULL
                                    AND owner_instance_id IS NOT NULL
                                ) THEN ? ELSE released_at END
                   WHERE task_id = ?""",
                (reason_code, time.time(), task_id),
            )

    def release_task(
        self,
        task_id: str,
        reason_code: str | None = None,
        *,
        owner_instance_id: str | None = None,
        lease_generation: int | None = None,
    ) -> bool:
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE task_admissions
                   SET state = 'released', released_at = ?, lease_expires_at = NULL,
                       reason_code = COALESCE(?, reason_code)
                   WHERE task_id = ? AND state != 'released'
                     AND (state IN ('preparing','queued')
                          OR (owner_instance_id = ? AND lease_generation = ?))
                     AND NOT EXISTS (
                         SELECT 1 FROM task_finalizations
                         WHERE task_finalizations.task_id = task_admissions.task_id
                           AND task_finalizations.state = 'pending'
                     )""",
                (
                    time.time(), reason_code, task_id,
                    owner_instance_id, lease_generation,
                ),
            ).rowcount == 1

    @staticmethod
    def _terminal_fields_json(terminal_fields: Mapping[str, Any]) -> str:
        fields = dict(terminal_fields)
        if fields.keys() != TERMINAL_FIELD_NAMES:
            raise ValueError("terminal_fields must contain only terminal Task fields")
        try:
            status = TaskStatus(fields["status"])
        except (TypeError, ValueError) as exc:
            raise ValueError("terminal_fields status must be terminal") from exc
        if not is_terminal(status):
            raise ValueError("terminal_fields status must be terminal")
        fields["status"] = status.value
        for name in TERMINAL_FIELD_NAMES - {"status"}:
            if fields[name] is not None and not isinstance(fields[name], str):
                raise ValueError(f"terminal_fields {name} must be a string or null")
        return json.dumps(
            {"version": 1, "fields": fields},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _terminal_fields(cls, fields_json: str) -> dict[str, Any]:
        try:
            payload = json.loads(fields_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid terminal fields JSON") from exc
        if (
            not isinstance(payload, dict)
            or payload.keys() != {"version", "fields"}
            or payload["version"] != 1
            or not isinstance(payload["fields"], dict)
            or cls._terminal_fields_json(payload["fields"]) != fields_json
        ):
            raise ValueError("invalid terminal fields JSON")
        return payload["fields"]

    def _stage_finalization(
        self,
        task_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        eligible_states: tuple[str, ...],
        borrowed_parent_task_id: str | None = None,
        require_parent_fence: bool = False,
        use_admission_reason: bool = False,
    ) -> tuple[str, str, dict[str, Any]] | None:
        with self.ledger.immediate() as conn:
            admission = conn.execute(
                """SELECT session_id, state, borrowed_parent_task_id, reason_code
                   FROM task_admissions
                   WHERE task_id = ? AND owner_instance_id = ?
                     AND lease_generation = ?""",
                (task_id, owner_instance_id, lease_generation),
            ).fetchone()
            if (
                admission is None
                or admission["state"] not in eligible_states
                or admission["borrowed_parent_task_id"] != borrowed_parent_task_id
            ):
                return None
            if require_parent_fence:
                parent = conn.execute(
                    """SELECT 1 FROM task_admissions
                       WHERE task_id = ? AND state IN ('live','stopping')
                         AND owner_instance_id = ? AND lease_generation = ?""",
                    (
                        borrowed_parent_task_id, owner_instance_id,
                        lease_generation,
                    ),
                ).fetchone()
                if parent is None:
                    return None
            fields = dict(terminal_fields)
            if use_admission_reason and admission["reason_code"] is not None:
                fields["reason_code"] = admission["reason_code"]
            fields_json = self._terminal_fields_json(fields)
            existing = conn.execute(
                """SELECT owner_instance_id, lease_generation, fields_json, state
                   FROM task_finalizations WHERE task_id = ?""",
                (task_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["owner_instance_id"] != owner_instance_id
                    or existing["lease_generation"] != lease_generation
                    or existing["fields_json"] != fields_json
                ):
                    return None
                return str(existing["state"]), fields_json, fields
            conn.execute(
                """INSERT INTO task_finalizations (
                       task_id, session_id, owner_instance_id, lease_generation,
                       fields_json, state, created_at
                   ) VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    task_id, admission["session_id"], owner_instance_id,
                    lease_generation, fields_json, time.time(),
                ),
            )
            return "pending", fields_json, fields

    def _complete_finalization(
        self,
        task_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        fields_json: str,
        reason_code: str | None,
        eligible_states: tuple[str, ...],
    ) -> bool:
        with self.ledger.immediate() as conn:
            intent = conn.execute(
                """SELECT state FROM task_finalizations
                   WHERE task_id = ? AND owner_instance_id = ?
                     AND lease_generation = ? AND fields_json = ?""",
                (task_id, owner_instance_id, lease_generation, fields_json),
            ).fetchone()
            if intent is None:
                return False
            if intent["state"] == "completed":
                return True
            placeholders = ",".join("?" for _ in eligible_states)
            changed = conn.execute(
                f"""UPDATE task_admissions
                    SET state = 'released', released_at = ?, lease_expires_at = NULL,
                        reason_code = COALESCE(?, reason_code)
                    WHERE task_id = ? AND owner_instance_id = ?
                      AND lease_generation = ? AND state IN ({placeholders})""",
                (
                    time.time(), reason_code, task_id, owner_instance_id,
                    lease_generation, *eligible_states,
                ),
            ).rowcount
            if changed != 1:
                return False
            conn.execute(
                """UPDATE task_finalizations
                   SET state = 'completed', completed_at = ?
                   WHERE task_id = ? AND state = 'pending'""",
                (time.time(), task_id),
            )
            return True

    def _finalize_with_intent(
        self,
        task_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        eligible_states: tuple[str, ...],
        mutate: Callable[[dict[str, Any]], Any],
        borrowed_parent_task_id: str | None = None,
        require_parent_fence: bool = False,
        use_admission_reason: bool = False,
    ) -> bool:
        staged = self._stage_finalization(
            task_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=eligible_states,
            borrowed_parent_task_id=borrowed_parent_task_id,
            require_parent_fence=require_parent_fence,
            use_admission_reason=use_admission_reason,
        )
        if staged is None:
            return False
        intent_state, fields_json, fields = staged
        if intent_state == "completed":
            return True
        mutate(fields)
        return self._complete_finalization(
            task_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            fields_json=fields_json,
            reason_code=fields["reason_code"],
            eligible_states=eligible_states,
        )

    def finalize_task(
        self,
        task_id: str,
        reason_code: str | None,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """Persist terminal intent, write TaskStore, then release this lease."""
        if terminal_fields.get("reason_code") != reason_code:
            raise ValueError("terminal_fields reason_code must match reason_code")
        return self._finalize_with_intent(
            task_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=("live", "stopping"),
            mutate=mutate,
        )

    def reconcile(
        self,
        *,
        task_lookup: Callable[[str, str], Task | None],
        write_terminal: Callable[[str, str, dict[str, Any]], Any] | None = None,
        mark_worker_lost: Callable[[str, str], Any],
        owner_is_alive: Callable[[str], bool],
        now: float | None = None,
    ) -> ReconcileResult:
        """Reconcile durable admissions without spanning task-store I/O."""
        current_time = time.time() if now is None else now
        pending = self.ledger.connection().execute(
            """SELECT task_id, session_id, owner_instance_id, lease_generation,
                      fields_json
               FROM task_finalizations WHERE state = 'pending'
               ORDER BY created_at"""
        ).fetchall()
        pending_task_ids = {str(row["task_id"]) for row in pending}
        finalization_conflicts = 0
        completed_pending: list[tuple[str, str]] = []
        for intent in pending:
            task = task_lookup(intent["session_id"], intent["task_id"])
            if task is None:
                continue
            try:
                fields = self._terminal_fields(intent["fields_json"])
            except ValueError:
                continue
            if not is_terminal(task.status):
                if write_terminal is None:
                    continue
                try:
                    write_terminal(intent["session_id"], intent["task_id"], fields)
                except Exception:
                    continue
                task = task_lookup(intent["session_id"], intent["task_id"])
                if task is None or not is_terminal(task.status):
                    continue
            actual_fields = {
                "status": task.status.value,
                "head_id": task.head_id,
                "result_text": task.result_text,
                "error": task.error,
                "reason_code": task.reason_code,
            }
            if actual_fields != fields:
                finalization_conflicts += 1
                continue
            completed = self._complete_finalization(
                intent["task_id"],
                owner_instance_id=intent["owner_instance_id"],
                lease_generation=intent["lease_generation"],
                fields_json=intent["fields_json"],
                reason_code=fields["reason_code"],
                eligible_states=("live", "stopping", "queued"),
            )
            if completed:
                completed_pending.append((intent["task_id"], intent["session_id"]))
        rows = self.ledger.connection().execute(
            """SELECT admission_id, task_id, session_id, budget_scope_id,
                      state, owner_instance_id, lease_generation, lease_expires_at
               FROM task_admissions WHERE state != 'released'
               ORDER BY admitted_seq"""
        ).fetchall()
        finalized = rolled_back = released_missing = released_lost = 0
        for row in rows:
            if row["task_id"] in pending_task_ids:
                continue
            state = row["state"]
            task = task_lookup(row["session_id"], row["task_id"])
            if state == "preparing":
                if task is not None and task.admission_id == row["admission_id"]:
                    with self.ledger.immediate() as conn:
                        changed = conn.execute(
                            """UPDATE task_admissions SET state = 'queued'
                               WHERE admission_id = ? AND state = 'preparing'""",
                            (row["admission_id"],),
                        ).rowcount
                    finalized += int(changed == 1)
                else:
                    with self.ledger.immediate() as conn:
                        deleted = conn.execute(
                            """DELETE FROM task_admissions
                               WHERE admission_id = ? AND state = 'preparing'""",
                            (row["admission_id"],),
                        ).rowcount
                        if deleted:
                            conn.execute(
                                "DELETE FROM budget_scopes WHERE budget_scope_id = ?",
                                (row["budget_scope_id"],),
                            )
                    rolled_back += int(deleted == 1)
                continue
            if state == "queued":
                if task is None:
                    with self.ledger.immediate() as conn:
                        changed = conn.execute(
                            """UPDATE task_admissions
                               SET state = 'released', reason_code = 'error.task_missing',
                                   released_at = ?
                               WHERE admission_id = ? AND state = 'queued'""",
                            (current_time, row["admission_id"]),
                        ).rowcount
                    released_missing += int(changed == 1)
                continue
            lease = row["lease_expires_at"]
            if lease is not None and float(lease) > current_time:
                continue
            owner = row["owner_instance_id"]
            if owner and owner_is_alive(owner):
                continue
            with self.ledger.immediate() as conn:
                fenced = conn.execute(
                    """UPDATE task_admissions
                       SET state = 'stopping', reason_code = 'error.worker_lost',
                           owner_instance_id = NULL,
                           lease_generation = lease_generation + 1,
                           lease_expires_at = NULL
                       WHERE admission_id = ? AND state IN ('live','stopping')
                         AND owner_instance_id IS ?
                         AND lease_generation = ?
                         AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                    (
                        row["admission_id"], owner,
                        row["lease_generation"], current_time,
                    ),
                ).rowcount
            if fenced != 1:
                continue
            try:
                mark_worker_lost(row["session_id"], row["task_id"])
            except Exception:
                continue
            with self.ledger.immediate() as conn:
                changed = conn.execute(
                    """UPDATE task_admissions
                       SET state = 'released', released_at = ?, lease_expires_at = NULL
                       WHERE admission_id = ? AND state = 'stopping'
                         AND owner_instance_id IS NULL
                         AND lease_generation = ?
                         AND reason_code = 'error.worker_lost'""",
                    (
                        current_time, row["admission_id"],
                        row["lease_generation"] + 1,
                    ),
                ).rowcount
            released_lost += int(changed == 1)
        return ReconcileResult(
            finalized_preparing=finalized,
            rolled_back_preparing=rolled_back,
            released_missing=released_missing,
            released_worker_lost=released_lost,
            finalization_conflicts=finalization_conflicts,
            completed_pending=tuple(completed_pending),
        )

    def task_time_limits(self, task_id: str) -> tuple[int | None, int | None]:
        return _durable_task_time_limits(self.ledger, task_id)

    def record_activity(
        self,
        task_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        activity_kind: str,
    ) -> bool:
        """Persist meaningful task activity for the task and live ancestors."""
        if activity_kind not in MEANINGFUL_ACTIVITY_KINDS:
            return False
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """WITH RECURSIVE lineage(task_id) AS (
                       SELECT ?
                       UNION ALL
                       SELECT a.parent_task_id
                       FROM task_admissions a
                       JOIN lineage l ON a.task_id = l.task_id
                       WHERE a.parent_task_id IS NOT NULL
                   )
                   SELECT task_id FROM task_admissions
                   WHERE task_id IN (SELECT task_id FROM lineage)
                     AND owner_instance_id = ?
                     AND lease_generation = ?
                     AND (
                         state IN ('live','stopping')
                         OR (
                             state = 'queued'
                             AND borrowed_parent_task_id IS NOT NULL
                         )
                     )""",
                (task_id, owner_instance_id, lease_generation),
            ).fetchall()
            if not rows:
                return False
            now = time.time()
            conn.executemany(
                "UPDATE task_admissions SET last_activity_at = ? WHERE task_id = ?",
                ((now, row["task_id"]) for row in rows),
            )
            return True

    @staticmethod
    def _scope_usage(conn, scope_id: str, kind: str) -> tuple[int, int]:
        usage = _scope_usage_breakdown(conn, scope_id)
        if kind == "token":
            used = usage["actual_tokens"] + usage["reserved_tokens"]
        else:
            used = usage["actual_cost_microusd"] + usage["reserved_cost_microusd"]
        return used, usage["unknown_cost_events"]

    def _reserve(
        self,
        task_id: str,
        *,
        kind: str,
        amount: int,
        price_known: bool = True,
    ) -> ReservationDecision:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError("reservation amount must be a positive integer")
        with self.ledger.immediate() as conn:
            admission = conn.execute(
                "SELECT budget_scope_id FROM task_admissions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if admission is None:
                return ReservationDecision(False, None, "quota.accounting_unavailable", True)
            scopes = conn.execute(
                """WITH RECURSIVE ancestors AS (
                    SELECT * FROM budget_scopes WHERE budget_scope_id = ?
                    UNION ALL
                    SELECT b.* FROM budget_scopes b
                    JOIN ancestors a ON a.parent_scope_id = b.budget_scope_id
                ) SELECT * FROM ancestors""",
                (admission["budget_scope_id"],),
            ).fetchall()
            limit_column = "max_total_tokens" if kind == "token" else "max_cost_microusd"
            if kind == "cost" and not price_known and any(
                scope[limit_column] is not None for scope in scopes
            ):
                return ReservationDecision(False, None, "quota.cost_unavailable", False)
            reason = "quota.token_exhausted" if kind == "token" else "quota.cost_exhausted"
            for scope in scopes:
                ceiling = scope[limit_column]
                used, unknown_cost = self._scope_usage(
                    conn, scope["budget_scope_id"], kind,
                )
                if kind == "cost" and ceiling is not None and unknown_cost:
                    return ReservationDecision(False, None, "quota.cost_unavailable", False)
                if ceiling is not None and used + amount > int(ceiling):
                    return ReservationDecision(False, None, reason, False)
            reservation_id = "res_" + uuid.uuid4().hex
            conn.execute(
                """INSERT INTO usage_reservations (
                    reservation_id, task_id, budget_scope_id, kind, state,
                    reserved_tokens, reserved_cost_microusd, expires_at
                ) VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)""",
                (
                    reservation_id, task_id, admission["budget_scope_id"], kind,
                    amount if kind == "token" else None,
                    amount if kind == "cost" else None,
                    time.time() + 300.0,
                ),
            )
            return ReservationDecision(True, reservation_id, None, False)

    def reserve_tokens(self, task_id: str, tokens: int) -> ReservationDecision:
        return self._reserve(task_id, kind="token", amount=tokens)

    def reserve_cost(
        self, task_id: str, cost_microusd: int, *, price_known: bool,
    ) -> ReservationDecision:
        return self._reserve(
            task_id, kind="cost", amount=cost_microusd, price_known=price_known,
        )

    def reserve_provider_request(
        self,
        task_id: str,
        *,
        input_token_upper_bound: int,
        requested_max_output_tokens: int,
        model: Any,
    ) -> RequestReservation:
        """Atomically reserve token and known-cost exposure for one request."""
        with self.ledger.immediate() as conn:
            admission = conn.execute(
                """SELECT budget_scope_id, session_id
                   FROM task_admissions WHERE task_id = ?""",
                (task_id,),
            ).fetchone()
            if admission is None:
                plan = plan_request_reservation(
                    input_token_upper_bound=input_token_upper_bound,
                    requested_max_output_tokens=requested_max_output_tokens,
                    remaining_token_budget=None,
                    model=model,
                )
                return replace(
                    plan, allowed=False, reason_code="quota.accounting_unavailable",
                )
            try:
                latest = self._session_limit_resolver(
                    admission["session_id"],
                ).effective_limits()
                latest_cost = (
                    ResourceLimits.usd_to_microusd(latest["max_cost_usd"])
                    if latest["max_cost_usd"] is not None else None
                )
            except Exception:
                plan = plan_request_reservation(
                    input_token_upper_bound=input_token_upper_bound,
                    requested_max_output_tokens=requested_max_output_tokens,
                    remaining_token_budget=None,
                    model=model,
                )
                return replace(
                    plan, allowed=False, reason_code="quota.accounting_unavailable",
                )
            conn.execute(
                """UPDATE budget_scopes
                   SET max_total_tokens = ?, max_cost_microusd = ?
                   WHERE scope_kind = 'session' AND session_id = ?""",
                (
                    latest["max_total_tokens"], latest_cost,
                    admission["session_id"],
                ),
            )
            scopes = conn.execute(
                """WITH RECURSIVE ancestors AS (
                    SELECT * FROM budget_scopes WHERE budget_scope_id = ?
                    UNION ALL
                    SELECT b.* FROM budget_scopes b
                    JOIN ancestors a ON a.parent_scope_id = b.budget_scope_id
                ) SELECT * FROM ancestors""",
                (admission["budget_scope_id"],),
            ).fetchall()

            token_remaining: list[int] = []
            for scope in scopes:
                ceiling = scope["max_total_tokens"]
                if ceiling is None:
                    continue
                used, _ = self._scope_usage(
                    conn, scope["budget_scope_id"], "token",
                )
                token_remaining.append(max(0, int(ceiling) - used))
            cost_budget_configured = any(
                scope["max_cost_microusd"] is not None for scope in scopes
            )
            plan = plan_request_reservation(
                input_token_upper_bound=input_token_upper_bound,
                requested_max_output_tokens=requested_max_output_tokens,
                remaining_token_budget=min(token_remaining) if token_remaining else None,
                model=model,
                cost_budget_configured=cost_budget_configured,
            )
            if not plan.allowed:
                return plan

            if plan.cost_known:
                assert plan.cost_reservation_microusd is not None
                for scope in scopes:
                    ceiling = scope["max_cost_microusd"]
                    if ceiling is None:
                        continue
                    used, unknown_cost = self._scope_usage(
                        conn, scope["budget_scope_id"], "cost",
                    )
                    if unknown_cost:
                        return replace(
                            plan, allowed=False, reason_code="quota.cost_unavailable",
                        )
                    if used + plan.cost_reservation_microusd > int(ceiling):
                        return replace(
                            plan, allowed=False, reason_code="quota.cost_exhausted",
                        )

            root_id = "res_" + uuid.uuid4().hex
            expires_at = time.time() + 300.0
            rows = [(
                root_id + ":token", task_id, admission["budget_scope_id"],
                "token", plan.token_reservation, None, expires_at,
            )]
            if plan.cost_known:
                rows.append((
                    root_id + ":cost", task_id, admission["budget_scope_id"],
                    "cost", None, plan.cost_reservation_microusd, expires_at,
                ))
            conn.executemany(
                """INSERT INTO usage_reservations (
                    reservation_id, task_id, budget_scope_id, kind, state,
                    reserved_tokens, reserved_cost_microusd, expires_at
                ) VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)""",
                rows,
            )
            return replace(plan, reservation_id=root_id)

    def start_provider_request(self, reservation_id: str) -> None:
        """Mark all reservations for a provider request as started."""
        with self.ledger.immediate() as conn:
            changed = conn.execute(
                """UPDATE usage_reservations
                   SET state = 'started', request_started_at = ?
                   WHERE reservation_id IN (?, ?) AND state = 'reserved'""",
                (
                    time.time(), reservation_id + ":token", reservation_id + ":cost",
                ),
            ).rowcount
            if changed == 0:
                existing = conn.execute(
                    """SELECT 1 FROM usage_reservations
                       WHERE reservation_id IN (?, ?)
                         AND state IN ('started','settled')""",
                    (reservation_id + ":token", reservation_id + ":cost"),
                ).fetchone()
                if existing is None:
                    raise KeyError(reservation_id)

    def settle_provider_request(self, reservation_id: str, event):
        """Append one actual usage event and settle both request reservations."""
        token_id = reservation_id + ":token"
        cost_id = reservation_id + ":cost"
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """SELECT task_id, budget_scope_id, state
                   FROM usage_reservations WHERE reservation_id IN (?, ?)""",
                (token_id, cost_id),
            ).fetchall()
            if not rows:
                raise KeyError(reservation_id)
            if all(row["state"] == "settled" for row in rows):
                return None
            if any(row["state"] == "released" for row in rows):
                raise RuntimeError("cannot settle a released provider reservation")
            attributed = event.model_copy(update={
                "task_id": rows[0]["task_id"],
                "budget_scope_id": rows[0]["budget_scope_id"],
                "reservation_id": token_id,
            })
            self.ledger.append_in_transaction(conn, attributed)
            conn.execute(
                """UPDATE usage_reservations
                   SET state = 'settled', settled_event_id = ?
                   WHERE reservation_id IN (?, ?)
                     AND state IN ('reserved','started')""",
                (attributed.event_id, token_id, cost_id),
            )
            return attributed

    def release_provider_request(self, reservation_id: str) -> bool:
        """Release a request the provider refused before it started billing.

        Only a reserved request is releasable. A started request may have
        reached the provider and keeps conservative exposure until settlement.
        """
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE usage_reservations SET state = 'released'
                   WHERE reservation_id IN (?, ?)
                     AND state = 'reserved'""",
                (reservation_id + ":token", reservation_id + ":cost"),
            ).rowcount > 0

    def recover_provider_reservations(self, *, now: float | None = None) -> int:
        """Release only expired requests that never reached provider start."""
        current_time = time.time() if now is None else now
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE usage_reservations SET state = 'released'
                   WHERE state = 'reserved' AND expires_at IS NOT NULL
                     AND expires_at <= ?""",
                (current_time,),
            ).rowcount

    def start_reservation(self, reservation_id: str) -> None:
        with self.ledger.immediate() as conn:
            conn.execute(
                """UPDATE usage_reservations
                   SET state = 'started', request_started_at = ?
                   WHERE reservation_id = ? AND state = 'reserved'""",
                (time.time(), reservation_id),
            )

    def settle_reservation(self, reservation_id: str, event) -> None:
        with self.ledger.immediate() as conn:
            reservation = conn.execute(
                """SELECT task_id, budget_scope_id, state
                   FROM usage_reservations WHERE reservation_id = ?""",
                (reservation_id,),
            ).fetchone()
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation["state"] == "settled":
                return
            attributed = event.model_copy(update={
                "task_id": reservation["task_id"],
                "budget_scope_id": reservation["budget_scope_id"],
                "reservation_id": reservation_id,
            })
            self.ledger.append_in_transaction(conn, attributed)
            conn.execute(
                """UPDATE usage_reservations
                   SET state = 'settled', settled_event_id = ?
                   WHERE reservation_id = ?""",
                (attributed.event_id, reservation_id),
            )


def _shared_remaining(ledger: UsageLedger, task_id: str) -> dict[str, Any]:
    conn = ledger.connection()
    scopes = conn.execute(
        """WITH RECURSIVE ancestors AS (
               SELECT parent.* FROM task_admissions admission
               JOIN budget_scopes current
                 ON current.budget_scope_id = admission.budget_scope_id
               JOIN budget_scopes parent
                 ON parent.budget_scope_id = current.parent_scope_id
               WHERE admission.task_id = ?
               UNION ALL
               SELECT parent.* FROM budget_scopes parent
               JOIN ancestors child
                 ON child.parent_scope_id = parent.budget_scope_id
           )
           SELECT * FROM ancestors""",
        (task_id,),
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


def build_task_resource_view(
    task: Task,
    *,
    ledger: UsageLedger,
    resolved: ResolvedResourceLimits,
) -> TaskResourceView:
    usage = ledger.task_resource_usage(task.id)
    counts = ledger.resource_counts(task.parent_session_id, task.id)
    snapshot = task.resolved_limits_snapshot
    snapshot_applied = False
    if snapshot and isinstance(snapshot, dict) and isinstance(snapshot.get("limits"), dict):
        fields = dict(resolved.fields)
        try:
            for name in ("max_runtime_seconds", "idle_timeout_seconds"):
                value = snapshot["limits"].get(name)
                if isinstance(value, dict):
                    fields[name] = ResolvedLimit(**value)
            resolved = ResolvedResourceLimits(
                scheduler_capacity=resolved.scheduler_capacity, fields=fields,
            )
            snapshot_applied = True
        except (TypeError, ValueError):
            snapshot_applied = False
    if task.admission_id and not snapshot_applied:
        runtime_limit, idle_limit = _durable_task_time_limits(ledger, task.id)
        fields = dict(resolved.fields)
        for name, value in (
            ("max_runtime_seconds", runtime_limit),
            ("idle_timeout_seconds", idle_limit),
        ):
            fields[name] = ResolvedLimit(value, value, "task")
        resolved = ResolvedResourceLimits(
            scheduler_capacity=resolved.scheduler_capacity, fields=fields,
        )
    limits = resolved.effective_limits()
    counts["session_live"]["limit"] = limits["max_live_per_session"]
    counts["session_queued"]["limit"] = limits["max_queued_per_session"]
    counts["session_tasks"]["limit"] = limits["max_tasks_per_session"]
    legacy = not task.admission_id
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
    reason = _reason_metadata(task.reason_code)
    runtime_used: float | None = None
    idle_used: float | None = None
    local_token_limit: int | None = None
    local_cost_limit: str | None = None
    if not legacy:
        timing = ledger.connection().execute(
            """SELECT admission.state, admission.started_at,
                      admission.last_activity_at, admission.released_at,
                      scope.max_total_tokens, scope.max_cost_microusd
               FROM task_admissions admission
               JOIN budget_scopes scope
                 ON scope.budget_scope_id = admission.budget_scope_id
               WHERE admission.task_id = ?""",
            (task.id,),
        ).fetchone()
        if timing is not None:
            local_token_limit = timing["max_total_tokens"]
            if timing["max_cost_microusd"] is not None:
                local_cost_limit = (
                    limits["max_cost_usd"]
                    if resolved.fields["max_cost_usd"].source == "task"
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
    return TaskResourceView(
        task_id=task.id,
        status=task.status.value,
        resource_state="legacy/unmetered" if legacy else counts["resource_state"],
        reason_code=task.reason_code,
        reason_key=reason["human_key"],
        retryable=reason["retryable"],
        limits=resolved.to_dict(),
        capacity={
            "scheduler_capacity": resolved.scheduler_capacity,
            "session_live": counts["session_live"],
            "session_queued": counts["session_queued"],
            "session_tasks": counts["session_tasks"],
            "queue_position": counts["queue_position"],
        },
        budget={
            "scope": "legacy/unmetered" if legacy else "task_with_shared_ancestors",
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
                if legacy else _shared_remaining(ledger, task.id)
            ),
        },
    )


__all__ = [
    "ResourceLimitError", "ResourceLimits", "ResolvedResourceLimits",
    "AdmissionDecision", "AdmissionRejected", "DispatchClaim", "ReconcileResult",
    "ReservationDecision",
    "RequestReservation", "plan_request_reservation",
    "ResourceGovernor", "TaskResourceView",
    "build_task_resource_view", "global_resource_limits",
    "resolve_resource_limits", "save_session_resource_limits",
    "scheduler_capacity", "session_resource_limits",
]
