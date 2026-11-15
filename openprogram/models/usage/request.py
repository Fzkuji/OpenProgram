"""Durable Goal request identity and replayable terminal usage receipts.

This is metering, not an execution journal: an unknown request is never replayed.
Only provider usage receipts are retried, with the same immutable event identity.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .context import UsageContext, request_context
from .event import UsageEvent
from .recorder import build_message_event, run_usage_hooks
import logging

_log = logging.getLogger(__name__)


def _exposure(conn, goal, sid, *, excluding=None):
    """Known usage plus conservative outstanding bounds, across all revisions."""
    from openprogram.models.providers.streaming.budget import QuotaExceeded
    legacy = goal.get("legacy_usage") or {}
    limits = goal.get("budget") or {}
    tokens = int(legacy.get("total_tokens") or 0)
    cost = Decimal(str(legacy.get("cost_usd") or 0)) * 1_000_000
    if limits.get("max_tokens") and not legacy.get("tokens_known", True):
        raise QuotaExceeded("quota.accounting_unavailable")
    if limits.get("max_cost_usd") is not None and not legacy.get("cost_known", True):
        raise QuotaExceeded("quota.cost_unavailable")
    for row in conn.execute("""SELECT * FROM usage_requests
            WHERE goal_session_id = ? AND goal_id = ? AND state != 'released'""", (sid, goal["goal_id"])):
        if row["request_id"] == excluding:
            continue
        metadata = json.loads(row["metadata_json"])
        reservation = metadata.get("reservation") or {}
        if row["state"] == "prepared" and reservation.get("expires_at", float("inf")) <= time.time():
            continue
        event = UsageEvent.model_validate_json(row["receipt_json"]) if row["receipt_json"] else None
        if event is not None and event.tokens_known:
            tokens += event.total_tokens
        elif reservation.get("token_reservation") is not None:
            tokens += reservation["token_reservation"]
        elif row["state"] != "prepared" and limits.get("max_tokens"):
            raise QuotaExceeded("quota.accounting_unavailable")
        if event is not None and event.cost_source != "unknown":
            cost += Decimal(str(event.cost_total)) * 1_000_000
        elif reservation.get("cost_reservation_microusd") is not None:
            cost += reservation["cost_reservation_microusd"]
        elif row["state"] != "prepared" and limits.get("max_cost_usd") is not None:
            raise QuotaExceeded("quota.cost_unavailable")
    return tokens, int(cost.to_integral_value(rounding=ROUND_CEILING))


def _reserve(conn, goal, sid, model, options, provider_context, provider):
    from openprogram.models.providers.streaming.budget import QuotaExceeded, estimate_input_upper_bound, requested_output_cap
    from openprogram.models.providers.catalog.api_registry import has_audited_accounting
    from openprogram.models.usage.reservation import plan_request_reservation
    limits = goal.get("budget") or {}
    token_limit, cost_limit = limits.get("max_tokens"), limits.get("max_cost_usd")
    if not token_limit and cost_limit is None:
        return None
    if (not has_audited_accounting(provider, model.api) or provider_context is None
            or getattr(options, "on_payload", None) is not None):
        raise QuotaExceeded("quota.accounting_unavailable", "Hard Goal budget requires an audited request bound")
    if cost_limit is not None and getattr(options, "service_tier", None) not in {None, "default", "standard"}:
        raise QuotaExceeded("quota.cost_unavailable", "Requested service tier has no fixed cost ceiling")
    tokens, cost = _exposure(conn, goal, sid)
    plan = plan_request_reservation(
        input_token_upper_bound=estimate_input_upper_bound(provider_context, options, model),
        requested_max_output_tokens=requested_output_cap(options, model),
        remaining_token_budget=max(0, token_limit - tokens) if token_limit else None,
        model=model, cost_budget_configured=cost_limit is not None,
    )
    if not plan.allowed:
        raise QuotaExceeded(plan.reason_code)
    if cost_limit is not None:
        ceiling = int((Decimal(str(cost_limit)) * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))
        if cost + plan.cost_reservation_microusd > ceiling:
            raise QuotaExceeded("quota.cost_exhausted")
    return dict(asdict(plan), expires_at=time.time() + 300)


# A Goal write of a request's check is redone this often when it meets a
# Goal newer than the one it read. With the lock held nothing else writes;
# after the wait for the lock ran out (``_goal_gate``) a writer can.
GOAL_WRITE_TRIES = 5

_IDENTITY = ("goal_id", "revision", "run_id")


def _permits_requests(goal, identity) -> bool:
    """Whether ``goal`` is the active Goal a request with ``identity`` belongs to."""
    return bool(goal and all(goal.get(key) == identity[key] for key in _IDENTITY)
                and goal.get("status") == "active" and not goal.get("stop_requested"))


def _admitted_goal(sid, identity, moment):
    """The Goal this request may spend from, read fresh; a refusal otherwise.

    A Goal that still counts usage the old way is converted here. That write
    is a compare-and-set on the Goal's version: when it meets a newer Goal
    the whole step (read, check, convert, write) is done again, as
    ``chat.turn_step`` redoes a running turn's step.
    """
    from openprogram.models.usage import goal_port as goals
    for attempt in range(GOAL_WRITE_TRIES):
        goal = goals.load_goal(sid)
        if not goal or any(goal.get(key) != identity[key] for key in _IDENTITY):
            raise goals.GoalConflictError(f"Goal changed before provider {moment}")
        if goal.get("status") != "active" or goal.get("stop_requested"):
            raise goals.GoalConflictError("Goal no longer permits provider requests")
        if goal.get("usage_mode") == "requests":
            return goal
        # Retain historical totals without assigning old receipts to a revision.
        goals.accumulate_goal_usage(sid, goal)
        if goal.get("usage_pending_until") is not None:
            raise goals.GoalStateUnavailable("Legacy Goal usage is unavailable")
        goal["legacy_usage"] = dict(goal.get("usage") or {})
        goal["usage_mode"] = "requests"
        try:
            goals.publish(sid, goal)
        except goals.GoalVersionConflict:
            if attempt == GOAL_WRITE_TRIES - 1:
                raise
            time.sleep(0.01)
            continue
        return goal


def _record_denial(sid, goal, exc):
    """Stop the Goal a request was refused for; the refusal itself is the
    caller's to raise.

    The write is redone on a Goal newer than the one read, as long as that
    Goal is still the active one the request belongs to: a Goal someone
    paused, edited or ended meanwhile is left as they set it.
    """
    from openprogram.models.usage import goal_port as goals
    identity = {key: goal.get(key) for key in _IDENTITY}
    exhausted = exc.reason_code in {"quota.cost_exhausted", "quota.token_exhausted"}
    for attempt in range(GOAL_WRITE_TRIES):
        goal.update(status="budget_exhausted" if exhausted else "paused_recoverable",
                    phase="terminal" if exhausted else "paused", last_reason=exc.reason_code)
        try:
            goals.publish(sid, goal)
            return
        except goals.GoalVersionConflict:
            if attempt == GOAL_WRITE_TRIES - 1:
                # The request is refused all the same; the turn's end closes
                # a Goal whose budget is spent.
                _log.warning("Goal of %s kept changing; its refusal %s was not recorded", sid, exc.reason_code)
                return
            time.sleep(0.01)
            goal = goals.load_goal(sid)
            if not _permits_requests(goal, identity):
                return


@contextmanager
def _goal_gate(sid):
    """The Goal lock around a provider request's check.

    Holders only read and write the Goal's state, so the wait is short. A
    lock that stays held anyway does not fail the request: the check runs
    without it, on a consistent read of the Goal and inside the ledger's own
    transaction. What the lock adds is the order against a pause committing
    at the same moment, and a paused Goal stops its execution itself.
    """
    from openprogram.models.usage import goal_port as goals
    with ExitStack() as stack:
        try:
            stack.enter_context(goals.locked(sid))
        except goals.GoalBusy:
            _log.warning("Goal lock of %s stayed held; provider request checked without it", sid)
        yield


class RequestReceipt:
    def __init__(self, ledger, request_id: str, metadata: dict):
        self.ledger = ledger
        self.request_id = request_id
        self.metadata = metadata

    @classmethod
    def begin(cls, model, options, *, provider_context=None, provider=None, budget=None):
        from . import ledger
        from openprogram.models.usage import goal_port as goals
        from openprogram.models.providers.streaming.budget import QuotaExceeded

        context = request_context(getattr(options, "session_id", None))
        if not context.goal_id:
            return None
        sid = context.goal_session_id
        if not sid:
            raise goals.GoalStateUnavailable("Goal request has no owning session")
        with _goal_gate(sid):
            goal = _admitted_goal(sid, {"goal_id": context.goal_id, "revision": context.goal_revision,
                                        "run_id": context.goal_run_id}, "request")
            now = time.time()
            metadata = {"context": asdict(context), "created_at": now,
                        "provider": model.provider, "api": model.api, "model_id": model.id,
                        "price": model.cost.model_dump(exclude_unset=True), "service_tier": getattr(options, "service_tier", None)}
            request_id = uuid.uuid4().hex
            try:
                with ledger.default_ledger.immediate() as conn:
                    metadata["reservation"] = _reserve(conn, goal, sid, model, options, provider_context, provider)
                    if budget is not None:
                        if budget._governor.ledger._path().resolve() != ledger.default_ledger._path().resolve():
                            raise QuotaExceeded("quota.accounting_unavailable", "Goal and Job accounting authorities differ")
                        metadata["job_reservation_id"] = budget.reservation.reservation_id
                        row = ledger.default_ledger.run_tables().handle.get("usage_reservations", budget.reservation.reservation_id + ":token")
                        if row is None:
                            raise QuotaExceeded("quota.accounting_unavailable", "Job reservation is unavailable")
                        metadata["job_id"], metadata["budget_scope_id"] = row["job_id"], row["budget_scope_id"]
                    conn.execute("""INSERT INTO usage_requests
                        (request_id, goal_session_id, goal_id, goal_revision, state, metadata_json, created_at)
                        VALUES (?, ?, ?, ?, 'prepared', ?, ?)""",
                        (request_id, sid, context.goal_id, context.goal_revision, json.dumps(metadata), now))
            except QuotaExceeded as exc:
                _record_denial(sid, goal, exc)
                raise
        return cls(ledger.default_ledger, request_id, metadata)

    def clamp(self, options, model):
        from openprogram.models.providers.streaming.budget import BudgetedRequest
        from openprogram.models.usage.reservation import RequestReservation
        reservation = self.metadata.get("reservation")
        if not reservation:
            return options
        plan = RequestReservation(**{k: v for k, v in reservation.items() if k != "expires_at"})
        return BudgetedRequest("", None, plan).clamp(options, model)

    @classmethod
    def load(cls, ledger, request_id: str):
        with ledger.read() as conn:
            row = conn.execute("SELECT metadata_json FROM usage_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise KeyError(request_id)
        return cls(ledger, request_id, json.loads(row[0]))

    def start(self):
        from openprogram.models.usage import goal_port as goals
        context = self.metadata["context"]
        with _goal_gate(context["goal_session_id"]):
            goal = goals.load_goal(context["goal_session_id"])
            if not _permits_requests(goal, {"goal_id": context["goal_id"], "revision": context["goal_revision"],
                                            "run_id": context["goal_run_id"]}):
                raise goals.GoalConflictError("Goal changed before provider dispatch")
            from openprogram.models.providers.streaming.budget import QuotaExceeded
            try:
                with self.ledger.immediate() as conn:
                    reservation = self.metadata.get("reservation") or {}
                    if reservation.get("expires_at", float("inf")) <= time.time():
                        raise QuotaExceeded("quota.accounting_unavailable", "Goal request reservation expired before dispatch")
                    limits = goal.get("budget") or {}
                    tokens, cost = _exposure(conn, goal, context["goal_session_id"], excluding=self.request_id)
                    for key, used, amount, reason in (
                        ("max_tokens", tokens, reservation.get("token_reservation"), "token"),
                        ("max_cost_usd", cost, reservation.get("cost_reservation_microusd"), "cost"),
                    ):
                        ceiling = limits.get(key)
                        if ceiling is None:
                            continue
                        if amount is None:
                            raise QuotaExceeded("quota.accounting_unavailable")
                        if reason == "cost":
                            ceiling = int((Decimal(str(ceiling)) * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))
                        if used + amount > ceiling:
                            raise QuotaExceeded(f"quota.{reason}_exhausted")
                    if conn.execute("UPDATE usage_requests SET state = 'started' WHERE request_id = ? AND state = 'prepared'",
                                    (self.request_id,)).rowcount != 1:
                        raise RuntimeError("A provider request may only start once")
                    if self.metadata.get("job_reservation_id"):
                        self.ledger.start_provider_reservation(conn, self.metadata["job_reservation_id"])
            except QuotaExceeded as exc:
                _record_denial(context["goal_session_id"], goal, exc)
                raise
        self.refresh()

    def refresh(self):
        from openprogram.models.usage.goal_port import refresh_usage
        try:
            refresh_usage(self.metadata["context"]["goal_session_id"])
        except Exception:
            # Durable state remains authoritative if the optional projection fails.
            _log.debug(
                "goal usage refresh failed for session %s",
                self.metadata["context"]["goal_session_id"], exc_info=True)

    def release(self):
        with self.ledger.immediate() as conn:
            conn.execute("UPDATE usage_requests SET state = 'released' WHERE request_id = ? AND state = 'prepared'",
                         (self.request_id,))

    def event(self, message, prefix=None):
        from openprogram.models.providers.types import Model, ModelCost
        data = self.metadata
        model = Model(id=data["model_id"], name=data["model_id"], api=data["api"], provider=data["provider"],
                      base_url="", cost=ModelCost(**data["price"]))
        # Pricing uses the request's tier, even if a provider omitted it in usage.
        message = message.model_copy(deep=True)
        if message.usage.requested_service_tier is None:
            message.usage.requested_service_tier = data["service_tier"]
        event = build_message_event(model, message, context=UsageContext(**data["context"]),
                                    request_id=self.request_id, timestamp=data["created_at"],
                                    prefix=prefix)
        if event is None or (not event.tokens_known and event.cost_source == "unknown"
                             and not event.total_tokens and not event.cost_total):
            return None
        if any(not math.isfinite(value) or value < 0 for value in (
                event.cost_total, event.cost_input, event.cost_output, event.cost_cache_read, event.cost_cache_write)):
            raise ValueError("Provider reported invalid cost")
        if data.get("job_reservation_id"):
            event = event.model_copy(update={"job_id": data["job_id"], "budget_scope_id": data["budget_scope_id"],
                                             "reservation_id": data["job_reservation_id"] + ":token"})
        return event

    def settle(self, message, prefix=None):
        event = self.event(message, prefix)
        if event is None:
            self.refresh()
            return None
        # Store the receipt before projecting aggregates. A later process can
        # replay this outbox even if the event append fails or the Goal ended.
        with self.ledger.immediate() as conn:
            row = conn.execute("SELECT state, receipt_json FROM usage_requests WHERE request_id = ?",
                               (self.request_id,)).fetchone()
            if row is None or row["state"] not in {"started", "settled"}:
                raise RuntimeError("Cannot settle a provider request that never started")
            if row["receipt_json"]:
                previous = UsageEvent.model_validate_json(row["receipt_json"])
                # The delivery process is not part of the provider's receipt.
                if previous.model_dump(exclude={"origin_pid"}) != event.model_dump(exclude={"origin_pid"}):
                    raise ValueError("Conflicting terminal usage receipt")
            else:
                conn.execute("UPDATE usage_requests SET receipt_json = ? WHERE request_id = ?",
                             (event.model_dump_json(), self.request_id))
        return self.flush()

    def observe(self, message):
        """Persist cumulative lower bounds; only a terminal receipt settles."""
        event = self.event(message)
        if event is None:
            return
        changed = False
        with self.ledger.immediate() as conn:
            row = conn.execute("SELECT state, observed_json, receipt_json FROM usage_requests WHERE request_id = ?",
                               (self.request_id,)).fetchone()
            if row is None or row["state"] != "started" or row["receipt_json"]:
                return
            prior = UsageEvent.model_validate_json(row["observed_json"]) if row["observed_json"] else None
            if prior:
                # Providers publish cumulative snapshots, sometimes repeated or
                # out of order. Do not add counters from two snapshots.
                event = event.model_copy(update={
                    "total_tokens": max(prior.total_tokens, event.total_tokens),
                    "cost_total": max(prior.cost_total, event.cost_total),
                })
            if prior is None or (event.total_tokens, event.cost_total) != (prior.total_tokens, prior.cost_total):
                conn.execute("UPDATE usage_requests SET observed_json = ? WHERE request_id = ?",
                             (event.model_dump_json(), self.request_id))
                changed = True
        if changed:
            self.refresh()

    def flush(self):
        with self.ledger.immediate() as conn:
            row = conn.execute("SELECT * FROM usage_requests WHERE request_id = ? AND state IN ('started','settled')",
                               (self.request_id,)).fetchone()
            if row is None or not row["receipt_json"]:
                return None
            event = self.ledger.flush_request(conn, row)
        run_usage_hooks(event)
        return event


def project(session_id: str, goal: dict) -> None:
    """Rebuild a Goal subtotal; status/revision changes do not discard receipts."""
    from .ledger import default_ledger
    current = default_ledger.goal_usage(session_id, goal["goal_id"])
    legacy = goal.get("legacy_usage") or {}
    usage = dict(goal.get("usage") or {})
    usage.update(current)
    for key in ("total_tokens", "cost_usd"):
        usage[key] += legacy.get(key) or 0
    for key in ("tokens_known", "cost_known"):
        usage[key] = current[key] and legacy.get(key, True)
    usage.pop("accounting_pending", None)
    goal.update(usage=usage, usage_accounted_at=time.time())
    goal.pop("usage_pending_until", None)
