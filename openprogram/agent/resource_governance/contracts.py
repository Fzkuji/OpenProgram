"""Resource governance: contracts."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_CEILING
from typing import Any


@dataclass(frozen=True)
class JobResourceView:
    job_id: str
    status: str
    resource_state: str
    reason_code: str | None
    reason_key: str | None
    retryable: bool
    limits: dict[str, Any]
    capacity: dict[str, Any]
    budget: dict[str, Any]
    execution_id: str | None = None
    capabilities: dict[str, Any] | None = None
    checkpoint_head_id: str | None = None
    event_cursor: dict[str, Any] | None = None
    resource: dict[str, Any] | None = None
    admission_id: str | None = None
    resource_lease_generation: int | None = None
    owner_instance_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.resource is None:
            value["resource"] = {
                "admission_id": self.admission_id,
                "resource_state": self.resource_state,
                "queue_wait": None,
                "resource_lease_generation": self.resource_lease_generation,
                "owner_instance_id": self.owner_instance_id,
                "limits": self.limits,
                "usage": self.budget,
                "reservation": None,
            }
        return value


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    job_id: str | None
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
    job_id: str
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
    worker_lost: tuple[tuple[str, str], ...] = ()

