"""Resource governance: usage."""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping
from openprogram.agent.job.types import Job
from openprogram.usage.ledger import UsageLedger
from .limits import (
    RESOURCE_REASON_METADATA,
    ResourceLimits,
)


def _durable_job_time_limits(
    ledger: UsageLedger, job_id: str,
) -> tuple[int | None, int | None]:
    with ledger.read() as conn:
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
               SELECT MIN(max_runtime_seconds), MIN(idle_timeout_seconds)
               FROM ancestors WHERE scope_kind = 'job'""",
            (job_id,),
        ).fetchone()
    return (None, None) if row is None else (row[0], row[1])


def _job_fingerprint(job: Job) -> str:
    facts = {
        name: getattr(job, name)
        for name in (
            "id", "parent_session_id", "prompt", "agent_id", "context_mode",
            "parent_msg_id", "parent_job_id", "caller_msg_id", "caller_session_id",
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

