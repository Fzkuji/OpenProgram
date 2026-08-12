"""Resource-limit parsing, inheritance, and read-only task diagnostics."""
from __future__ import annotations

import os
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from openprogram.agent.task.types import Task
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
                if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
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
    retryable: bool
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


class AdmissionRejected(RuntimeError):
    def __init__(self, decision: AdmissionDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason_code or "resource admission rejected")


@dataclass(frozen=True)
class ReservationDecision:
    accepted: bool
    reservation_id: str | None
    reason_code: str | None
    retryable: bool


@dataclass(frozen=True)
class DispatchClaim:
    task_id: str
    session_id: str


@dataclass(frozen=True)
class ReconcileResult:
    finalized_preparing: int = 0
    rolled_back_preparing: int = 0
    released_missing: int = 0
    released_worker_lost: int = 0


def _task_fingerprint(task: Task) -> str:
    facts = {
        name: getattr(task, name)
        for name in (
            "id", "parent_session_id", "prompt", "agent_id", "context_mode",
            "parent_msg_id", "parent_task_id", "caller_msg_id", "caller_session_id",
            "chain_messages", "chain_generations", "caller_chain_generations",
            "worktree_id", "wait", "archive_when_done",
        )
    }
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
        task_limits = ResourceLimits.from_mapping(task.effective_limits or {})
        return resolve_resource_limits(
            global_resource_limits(), session=session_resource_limits(session_id),
            task=task_limits,
        )

    @staticmethod
    def _resolve_session_limits(session_id: str) -> ResolvedResourceLimits:
        return resolve_resource_limits(
            global_resource_limits(), session=session_resource_limits(session_id),
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
        return {
            "scheduler_capacity": resolved.scheduler_capacity,
            "session_live": {"used": int(row[0] or 0), "limit": limits["max_live_per_session"]},
            "session_queued": {"used": int(row[1] or 0), "limit": limits["max_queued_per_session"]},
            "session_tasks": {"used": int(row[2] or 0), "limit": limits["max_tasks_per_session"]},
        }

    @staticmethod
    def _denied(
        reason_code: str,
        *,
        resolved: ResolvedResourceLimits,
        capacity: dict[str, Any],
        retryable: bool,
    ) -> AdmissionDecision:
        return AdmissionDecision(
            False, None, reason_code, retryable, resolved.to_dict(), capacity,
        )

    def admit_task(
        self,
        task: Task,
        *,
        persist: Callable[[Task], Any],
        creates_agent: bool = True,
        caller_turn_id: str | None = None,
    ) -> AdmissionDecision:
        try:
            resolved = self._limit_resolver(task.parent_session_id, task)
        except ResourceLimitError:
            fallback = resolve_resource_limits(ResourceLimits())
            return self._denied(
                "quota.invalid_limits", resolved=fallback,
                capacity={"scheduler_capacity": fallback.scheduler_capacity}, retryable=False,
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

        with self.ledger.immediate() as conn:
            existing = conn.execute(
                """SELECT admission_id, request_fingerprint, budget_scope_id, state
                   FROM task_admissions WHERE task_id = ?""",
                (task.id,),
            ).fetchone()
            capacity = self._capacity(conn, task.parent_session_id, resolved)
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    return self._denied(
                        "quota.admission_conflict", resolved=resolved,
                        capacity=capacity, retryable=False,
                    )
                task.admission_id = existing["admission_id"]
                task.budget_scope_id = existing["budget_scope_id"]
                task.effective_limits = effective
                return AdmissionDecision(
                    True, task.id, None, False, resolved.to_dict(), capacity, True,
                )
            queued = capacity["session_queued"]
            if queued["limit"] is not None and queued["used"] >= queued["limit"]:
                return self._denied(
                    "quota.queue_full", resolved=resolved,
                    capacity=capacity, retryable=True,
                )
            cumulative = capacity["session_tasks"]
            if cumulative["limit"] is not None and cumulative["used"] >= cumulative["limit"]:
                return self._denied(
                    "quota.tasks_exhausted", resolved=resolved,
                    capacity=capacity, retryable=False,
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
                    effective["max_total_tokens"],
                    ResourceLimits.usd_to_microusd(effective["max_cost_usd"])
                    if effective["max_cost_usd"] is not None else None,
                    effective["max_runtime_seconds"], effective["idle_timeout_seconds"],
                    time.time(),
                ),
            )
            conn.execute(
                """INSERT INTO task_admissions (
                    admission_id, task_id, session_id, parent_task_id,
                    caller_turn_id, creates_agent, request_fingerprint,
                    budget_scope_id, state, admitted_seq, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'preparing', ?, ?)""",
                (
                    admission_id, task.id, task.parent_session_id, task.parent_task_id,
                    caller_turn_id, int(creates_agent), fingerprint, scope_id,
                    admitted_seq, time.time(),
                ),
            )

        task.admission_id = admission_id
        task.budget_scope_id = scope_id
        task.effective_limits = effective
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
        return AdmissionDecision(
            True, task.id, None, False, resolved.to_dict(), capacity,
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

    def try_start(self, task_id: str, *, owner_instance_id: str) -> bool:
        """Atomically exchange queued capacity for live capacity."""
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
                       last_activity_at = ?, lease_expires_at = ?
                   WHERE task_id = ? AND state = 'queued'""",
                (owner_instance_id, now, now, now + 30.0, task_id),
            ).rowcount
            return changed == 1

    def claim_next(self, *, owner_instance_id: str) -> DispatchClaim | None:
        """Claim the globally oldest queued task whose session is eligible."""
        with self.ledger.immediate() as conn:
            queued = conn.execute(
                """SELECT task_id, session_id FROM task_admissions
                   WHERE state = 'queued' ORDER BY admitted_seq"""
            ).fetchall()
            for candidate in queued:
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
                           last_activity_at = ?, lease_expires_at = ?
                       WHERE task_id = ? AND state = 'queued'""",
                    (
                        owner_instance_id, now, now, now + 30.0,
                        candidate["task_id"],
                    ),
                ).rowcount
                if changed == 1:
                    return DispatchClaim(
                        candidate["task_id"], candidate["session_id"],
                    )
            return None

    def renew_lease(self, task_id: str, *, owner_instance_id: str) -> bool:
        with self.ledger.immediate() as conn:
            now = time.time()
            return conn.execute(
                """UPDATE task_admissions SET lease_expires_at = ?
                   WHERE task_id = ? AND owner_instance_id = ?
                     AND state IN ('live','stopping')""",
                (now + 30.0, task_id, owner_instance_id),
            ).rowcount == 1

    def request_stop(self, task_id: str, reason_code: str) -> None:
        with self.ledger.immediate() as conn:
            conn.execute(
                """UPDATE task_admissions
                   SET state = CASE
                       WHEN state = 'live' THEN 'stopping'
                       WHEN state IN ('preparing','queued') THEN 'released'
                       ELSE state END,
                       reason_code = ?,
                       released_at = CASE
                           WHEN state IN ('preparing','queued') THEN ? ELSE released_at END
                   WHERE task_id = ?""",
                (reason_code, time.time(), task_id),
            )

    def release_task(
        self,
        task_id: str,
        reason_code: str | None = None,
        *,
        owner_instance_id: str | None = None,
    ) -> bool:
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE task_admissions
                   SET state = 'released', released_at = ?, lease_expires_at = NULL,
                       reason_code = COALESCE(?, reason_code)
                   WHERE task_id = ? AND state != 'released'
                     AND (state IN ('preparing','queued')
                          OR owner_instance_id = ?)""",
                (time.time(), reason_code, task_id, owner_instance_id),
            ).rowcount == 1

    def reconcile(
        self,
        *,
        task_lookup: Callable[[str, str], Task | None],
        mark_worker_lost: Callable[[str, str], Any],
        owner_is_alive: Callable[[str], bool],
        now: float | None = None,
    ) -> ReconcileResult:
        """Reconcile durable admissions without spanning task-store I/O."""
        current_time = time.time() if now is None else now
        rows = self.ledger.connection().execute(
            """SELECT admission_id, task_id, session_id, budget_scope_id,
                      state, owner_instance_id, lease_expires_at
               FROM task_admissions WHERE state != 'released'
               ORDER BY admitted_seq"""
        ).fetchall()
        finalized = rolled_back = released_missing = released_lost = 0
        for row in rows:
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
                       SET state = 'stopping', reason_code = 'error.worker_lost'
                       WHERE admission_id = ? AND state IN ('live','stopping')
                         AND owner_instance_id IS ?
                         AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                    (row["admission_id"], owner, current_time),
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
                         AND owner_instance_id IS ?
                         AND reason_code = 'error.worker_lost'""",
                    (current_time, row["admission_id"], owner),
                ).rowcount
            released_lost += int(changed == 1)
        return ReconcileResult(
            finalized_preparing=finalized,
            rolled_back_preparing=rolled_back,
            released_missing=released_missing,
            released_worker_lost=released_lost,
        )

    @staticmethod
    def _scope_usage(conn, scope_id: str, kind: str) -> tuple[int, int]:
        metric = "total_tokens" if kind == "token" else "CAST(ROUND(cost_total * 1000000) AS INTEGER)"
        reserved = "reserved_tokens" if kind == "token" else "reserved_cost_microusd"
        actual = conn.execute(
            f"""WITH RECURSIVE descendants(id) AS (
                    SELECT ? UNION ALL
                    SELECT b.budget_scope_id FROM budget_scopes b
                    JOIN descendants d ON b.parent_scope_id = d.id
                )
                SELECT COALESCE(SUM({metric}), 0),
                       SUM(CASE WHEN COALESCE(cost_source, 'unknown') = 'unknown'
                                THEN 1 ELSE 0 END)
                FROM usage_events WHERE budget_scope_id IN (SELECT id FROM descendants)""",
            (scope_id,),
        ).fetchone()
        open_reserved = conn.execute(
            f"""WITH RECURSIVE descendants(id) AS (
                    SELECT ? UNION ALL
                    SELECT b.budget_scope_id FROM budget_scopes b
                    JOIN descendants d ON b.parent_scope_id = d.id
                )
                SELECT COALESCE(SUM({reserved}), 0)
                FROM usage_reservations
                WHERE budget_scope_id IN (SELECT id FROM descendants)
                  AND kind = ? AND state IN ('reserved','started')""",
            (scope_id, kind),
        ).fetchone()[0]
        return int(actual[0] or 0) + int(open_reserved or 0), int(actual[1] or 0)

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


def build_task_resource_view(
    task: Task,
    *,
    ledger: UsageLedger,
    resolved: ResolvedResourceLimits,
) -> TaskResourceView:
    usage = ledger.task_usage(task.id)
    counts = ledger.resource_counts(task.parent_session_id, task.id)
    limits = resolved.effective_limits()
    counts["session_live"]["limit"] = limits["max_live_per_session"]
    counts["session_queued"]["limit"] = limits["max_queued_per_session"]
    counts["session_tasks"]["limit"] = limits["max_tasks_per_session"]
    cost_known = usage.unknown_cost_events == 0
    actual_cost = str(Decimal(str(usage.cost_total))) if cost_known else None
    legacy = not task.admission_id
    return TaskResourceView(
        task_id=task.id,
        status=task.status.value,
        resource_state="legacy/unmetered" if legacy else counts["resource_state"],
        reason_code=task.reason_code,
        retryable=False,
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
                "actual": usage.total_tokens,
                "reserved": counts["reserved_tokens"],
                "limit": limits["max_total_tokens"],
            },
            "cost_usd": {
                "actual": actual_cost,
                "reserved": str(ResourceLimits.microusd_to_usd(counts["reserved_cost_microusd"])),
                "limit": limits["max_cost_usd"],
                "known": cost_known,
                "unknown_events": usage.unknown_cost_events,
            },
            "runtime_seconds": {"used": None, "limit": limits["max_runtime_seconds"]},
            "idle_seconds": {"used": None, "limit": limits["idle_timeout_seconds"]},
            "shared_remaining": {"tokens": None, "cost_usd": None},
        },
    )


__all__ = [
    "ResourceLimitError", "ResourceLimits", "ResolvedResourceLimits",
    "AdmissionDecision", "AdmissionRejected", "DispatchClaim", "ReconcileResult",
    "ReservationDecision",
    "ResourceGovernor", "TaskResourceView",
    "build_task_resource_view", "global_resource_limits",
    "resolve_resource_limits", "save_session_resource_limits",
    "scheduler_capacity", "session_resource_limits",
]
