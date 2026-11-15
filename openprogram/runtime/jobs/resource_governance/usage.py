"""Resource governance: usage."""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping
from openprogram.runtime.jobs.types import Job
from openprogram.models.usage.ledger import UsageLedger
from openprogram.runtime.jobs.resource_governance.limits import (
    RESOURCE_REASON_METADATA,
    ResourceLimits,
)


def _scope_chain(tables, scope_id: str | None) -> list[dict]:
    """A budget scope followed by each scope above it."""
    chain: list[dict] = []
    seen: set[str] = set()
    while scope_id is not None and scope_id not in seen:
        seen.add(scope_id)
        scope = tables.get("budget_scopes", scope_id)
        if scope is None:
            break
        chain.append(scope)
        scope_id = scope["parent_scope_id"]
    return chain


def _job_scope_chain(ledger: UsageLedger, tables, job_id: str) -> list[dict]:
    ledger.ensure_job_session(tables, job_id)
    admission = tables.first("job_admissions", job_id=job_id)
    return [] if admission is None else _scope_chain(tables, admission["budget_scope_id"])


def _smallest(rows: list[dict], column: str) -> Any:
    return min((row[column] for row in rows if row[column] is not None), default=None)


def _durable_job_time_limits(
    ledger: UsageLedger, job_id: str,
) -> tuple[int | None, int | None]:
    tables = ledger.run_tables().handle
    chain = [
        scope for scope in _job_scope_chain(ledger, tables, job_id)
        if scope["scope_kind"] == "job"
    ]
    return _smallest(chain, "max_runtime_seconds"), _smallest(chain, "idle_timeout_seconds")


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


def _scope_descendants(tables, scope_id: str) -> list[str]:
    """A budget scope's id and the id of every scope under it."""
    found = [scope_id]
    seen = {scope_id}
    frontier = [scope_id]
    while frontier:
        children = [
            row["budget_scope_id"]
            for row in tables.rows("budget_scopes", parent_scope_id=frontier)
            if row["budget_scope_id"] not in seen
        ]
        seen.update(children)
        found.extend(children)
        frontier = children
    return found


def _scope_usage_breakdown(ledger: UsageLedger, tables, scope_id: str) -> dict[str, int]:
    scope_ids = _scope_descendants(tables, scope_id)
    for session_id in {
        row["session_id"] for row in tables.rows("budget_scopes", budget_scope_id=scope_ids)
    }:
        tables.tables.ensure_session(session_id)
    # A settlement commits its event before it clears the reservation. Reading
    # reservations under the run tables' lock and the events after them can
    # only count one twice for a moment, never miss it.
    reservations = tables.rows(
        "usage_reservations", budget_scope_id=scope_ids, state=("reserved", "started"),
    )
    events = ledger.scope_usage_events(scope_ids)
    return {
        "actual_tokens": sum(int(row["total_tokens"] or 0) for row in events),
        "actual_cost_microusd": sum(
            _cost_to_microusd(row["cost_total"] or 0) for row in events
        ),
        "unknown_cost_events": sum(
            (row["cost_source"] or "unknown") == "unknown" for row in events
        ),
        "reserved_tokens": sum(int(row["reserved_tokens"] or 0) for row in reservations),
        "reserved_cost_microusd": sum(
            int(row["reserved_cost_microusd"] or 0) for row in reservations
        ),
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

