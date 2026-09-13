"""Resource governance: reservations."""
from __future__ import annotations
import time
import uuid
from dataclasses import replace
from typing import Any
from .limits import (
    MEANINGFUL_ACTIVITY_KINDS,
    ResourceLimits,
)
from .contracts import (
    RequestReservation,
    ReservationDecision,
    plan_request_reservation,
)
from .usage import (
    _durable_job_time_limits,
    _scope_usage_breakdown,
)


class ReservationsOperations:
    def job_time_limits(self, job_id: str) -> tuple[int | None, int | None]:
        return _durable_job_time_limits(self.ledger, job_id)


    def record_activity(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        activity_kind: str,
    ) -> bool:
        """Persist meaningful job activity for the job and live ancestors."""
        if activity_kind not in MEANINGFUL_ACTIVITY_KINDS:
            return False
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """WITH RECURSIVE lineage(job_id) AS (
                       SELECT ?
                       UNION ALL
                       SELECT a.parent_job_id
                       FROM job_admissions a
                       JOIN lineage l ON a.job_id = l.job_id
                       WHERE a.parent_job_id IS NOT NULL
                   )
                   SELECT job_id FROM job_admissions
                   WHERE job_id IN (SELECT job_id FROM lineage)
                     AND owner_instance_id = ?
                     AND lease_generation = ?
                     AND (
                         state IN ('live','stopping')
                         OR (
                             state = 'queued'
                             AND borrowed_parent_job_id IS NOT NULL
                         )
                     )""",
                (job_id, owner_instance_id, lease_generation),
            ).fetchall()
            if not rows:
                return False
            now = time.time()
            conn.executemany(
                "UPDATE job_admissions SET last_activity_at = ? WHERE job_id = ?",
                ((now, row["job_id"]) for row in rows),
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
        job_id: str,
        *,
        kind: str,
        amount: int,
        price_known: bool = True,
    ) -> ReservationDecision:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError("reservation amount must be a positive integer")
        with self.ledger.immediate() as conn:
            admission = conn.execute(
                "SELECT budget_scope_id FROM job_admissions WHERE job_id = ?",
                (job_id,),
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
                    reservation_id, job_id, budget_scope_id, kind, state,
                    reserved_tokens, reserved_cost_microusd, expires_at
                ) VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)""",
                (
                    reservation_id, job_id, admission["budget_scope_id"], kind,
                    amount if kind == "token" else None,
                    amount if kind == "cost" else None,
                    time.time() + 300.0,
                ),
            )
            return ReservationDecision(True, reservation_id, None, False)


    def reserve_tokens(self, job_id: str, tokens: int) -> ReservationDecision:
        return self._reserve(job_id, kind="token", amount=tokens)


    def reserve_cost(
        self, job_id: str, cost_microusd: int, *, price_known: bool,
    ) -> ReservationDecision:
        return self._reserve(
            job_id, kind="cost", amount=cost_microusd, price_known=price_known,
        )


    def reserve_provider_request(
        self,
        job_id: str,
        *,
        input_token_upper_bound: int,
        requested_max_output_tokens: int,
        model: Any,
    ) -> RequestReservation:
        """Atomically reserve token and known-cost exposure for one request."""
        with self.ledger.immediate() as conn:
            admission = conn.execute(
                """SELECT budget_scope_id, session_id
                   FROM job_admissions WHERE job_id = ?""",
                (job_id,),
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
                root_id + ":token", job_id, admission["budget_scope_id"],
                "token", plan.token_reservation, None, expires_at,
            )]
            if plan.cost_known:
                rows.append((
                    root_id + ":cost", job_id, admission["budget_scope_id"],
                    "cost", None, plan.cost_reservation_microusd, expires_at,
                ))
            conn.executemany(
                """INSERT INTO usage_reservations (
                    reservation_id, job_id, budget_scope_id, kind, state,
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
                """SELECT job_id, budget_scope_id, state
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
                "job_id": rows[0]["job_id"],
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
                """SELECT job_id, budget_scope_id, state
                   FROM usage_reservations WHERE reservation_id = ?""",
                (reservation_id,),
            ).fetchone()
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation["state"] == "settled":
                return
            attributed = event.model_copy(update={
                "job_id": reservation["job_id"],
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

