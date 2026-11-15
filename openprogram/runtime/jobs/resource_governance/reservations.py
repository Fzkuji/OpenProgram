"""Resource governance: reservations."""
from __future__ import annotations
import time
import uuid
from dataclasses import replace
from typing import Any
from openprogram.runtime.jobs.resource_governance.limits import (
    MEANINGFUL_ACTIVITY_KINDS,
    ResourceLimits,
)
from openprogram.runtime.jobs.resource_governance.contracts import ReservationDecision
from openprogram.models.usage.reservation import (
    RequestReservation,
    plan_request_reservation,
)
from openprogram.runtime.jobs.resource_governance.usage import (
    _durable_job_time_limits,
    _scope_chain,
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
        with self._write() as tables:
            current: str | None = job_id
            seen: set[str] = set()
            claimed: list[str] = []
            while current is not None and current not in seen:
                seen.add(current)
                admission = self._admission(tables, current)
                if admission is None:
                    break
                if (
                    admission["owner_instance_id"] == owner_instance_id
                    and admission["lease_generation"] == lease_generation
                    and (
                        admission["state"] in ("live", "stopping")
                        or (
                            admission["state"] == "queued"
                            and admission["borrowed_parent_job_id"] is not None
                        )
                    )
                ):
                    claimed.append(current)
                current = admission["parent_job_id"]
            if not claimed:
                return False
            tables.update(
                "job_admissions", {"last_activity_at": time.time()}, job_id=claimed,
            )
            return True


    def _scope_usage(self, tables, scope_id: str, kind: str) -> tuple[int, int]:
        usage = _scope_usage_breakdown(self.ledger, tables, scope_id)
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
        with self._write() as tables:
            admission = self._admission(tables, job_id)
            if admission is None:
                return ReservationDecision(False, None, "quota.accounting_unavailable", True)
            scopes = _scope_chain(tables, admission["budget_scope_id"])
            limit_column = "max_total_tokens" if kind == "token" else "max_cost_microusd"
            if kind == "cost" and not price_known and any(
                scope[limit_column] is not None for scope in scopes
            ):
                return ReservationDecision(False, None, "quota.cost_unavailable", False)
            reason = "quota.token_exhausted" if kind == "token" else "quota.cost_exhausted"
            for scope in scopes:
                ceiling = scope[limit_column]
                used, unknown_cost = self._scope_usage(
                    tables, scope["budget_scope_id"], kind,
                )
                if kind == "cost" and ceiling is not None and unknown_cost:
                    return ReservationDecision(False, None, "quota.cost_unavailable", False)
                if ceiling is not None and used + amount > int(ceiling):
                    return ReservationDecision(False, None, reason, False)
            reservation_id = "res_" + uuid.uuid4().hex
            tables.insert("usage_reservations", {
                "reservation_id": reservation_id, "job_id": job_id,
                "session_id": admission["session_id"],
                "budget_scope_id": admission["budget_scope_id"], "kind": kind,
                "state": "reserved",
                "reserved_tokens": amount if kind == "token" else None,
                "reserved_cost_microusd": amount if kind == "cost" else None,
                "expires_at": time.time() + 300.0,
            })
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
        with self._write() as tables:
            admission = self._admission(tables, job_id)
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
            tables.update(
                "budget_scopes",
                {"max_total_tokens": latest["max_total_tokens"], "max_cost_microusd": latest_cost},
                scope_kind="session", session_id=admission["session_id"],
                where=lambda row: (
                    row["max_total_tokens"], row["max_cost_microusd"],
                ) != (latest["max_total_tokens"], latest_cost),
            )
            scopes = _scope_chain(tables, admission["budget_scope_id"])

            token_remaining: list[int] = []
            for scope in scopes:
                ceiling = scope["max_total_tokens"]
                if ceiling is None:
                    continue
                used, _ = self._scope_usage(
                    tables, scope["budget_scope_id"], "token",
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
                        tables, scope["budget_scope_id"], "cost",
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
            shared = {
                "job_id": job_id, "session_id": admission["session_id"],
                "budget_scope_id": admission["budget_scope_id"],
                "state": "reserved", "expires_at": expires_at,
            }
            tables.insert("usage_reservations", {
                **shared, "reservation_id": root_id + ":token", "kind": "token",
                "reserved_tokens": plan.token_reservation,
            })
            if plan.cost_known:
                tables.insert("usage_reservations", {
                    **shared, "reservation_id": root_id + ":cost", "kind": "cost",
                    "reserved_cost_microusd": plan.cost_reservation_microusd,
                })
            return replace(plan, reservation_id=root_id)


    def start_provider_request(self, reservation_id: str) -> None:
        """Mark all reservations for a provider request as started."""
        with self._write() as tables:
            self.ledger.check_reservation_startable(tables, reservation_id)
            self.ledger.mark_reservation_started(tables, reservation_id)


    def settle_provider_request(self, reservation_id: str, event):
        """Append one actual usage event, then settle both request reservations."""
        tables = self._read()
        with self.ledger.immediate() as conn:
            rows = tables.rows(
                "usage_reservations",
                reservation_id=[reservation_id + ":token", reservation_id + ":cost"],
            )
            if not rows:
                raise KeyError(reservation_id)
            if all(row["state"] == "settled" for row in rows):
                return None
            if any(row["state"] == "released" for row in rows):
                raise RuntimeError("cannot settle a released provider reservation")
            return self.ledger.settle_provider_reservation(conn, reservation_id, event)


    def release_provider_request(self, reservation_id: str) -> bool:
        """Release a request the provider refused before it started billing.

        Only a reserved request is releasable. A started request may have
        reached the provider and keeps conservative exposure until settlement.
        """
        with self._write() as tables:
            return tables.update(
                "usage_reservations", {"state": "released"},
                reservation_id=[reservation_id + ":token", reservation_id + ":cost"],
                state="reserved",
            ) > 0


    def _settle_recorded_reservations(self, tables) -> None:
        """Settle open reservations whose event is already in the ledger.

        A settlement records the event first, so a stop before the reservation
        changed leaves exactly this pair behind.
        """
        open_rows = tables.rows("usage_reservations", state=("reserved", "started"))
        recorded_as = {
            row["reservation_id"]: (
                row["reservation_id"].rsplit(":", 1)[0] + ":token"
                if row["reservation_id"].endswith((":token", ":cost"))
                else row["reservation_id"]
            )
            for row in open_rows
        }
        events = {
            row["reservation_id"]: row
            for row in self.ledger.reservation_events(set(recorded_as.values()))
        }
        for row in open_rows:
            reservation_id = row["reservation_id"]
            event = events.get(recorded_as[reservation_id])
            if event is None:
                continue
            if reservation_id.endswith(":token") and not event["tokens_known"]:
                continue
            if reservation_id.endswith(":cost") and (event["cost_source"] or "unknown") == "unknown":
                continue
            self.ledger.mark_reservations_settled(tables, [reservation_id], event["event_id"])


    def recover_provider_reservations(self, *, now: float | None = None) -> int:
        """Finish interrupted settlements, then release expired unstarted requests."""
        current_time = time.time() if now is None else now
        with self._write() as tables:
            self._settle_recorded_reservations(tables)
            return tables.update(
                "usage_reservations", {"state": "released"}, state="reserved",
                where=lambda row: (
                    row["expires_at"] is not None and row["expires_at"] <= current_time
                ),
            )


    def start_reservation(self, reservation_id: str) -> None:
        with self._write() as tables:
            tables.update(
                "usage_reservations",
                {"state": "started", "request_started_at": time.time()},
                reservation_id=reservation_id, state="reserved",
            )


    def settle_reservation(self, reservation_id: str, event) -> None:
        run_tables = self._tables()
        with self.ledger.immediate() as conn:
            reservation = run_tables.handle.get("usage_reservations", reservation_id)
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation["state"] == "settled":
                return
            attributed = event.model_copy(update={
                "job_id": reservation["job_id"],
                "budget_scope_id": reservation["budget_scope_id"],
                "reservation_id": reservation_id,
            })

            def settle() -> None:
                with run_tables.transaction() as tables:
                    tables.update(
                        "usage_reservations",
                        {"state": "settled", "settled_event_id": attributed.event_id},
                        reservation_id=reservation_id,
                    )

            self.ledger.append_in_transaction(conn, attributed)
            self.ledger.after_commit(settle)
