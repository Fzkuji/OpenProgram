"""Safe bounds for one provider request before it is sent.

Budgeting a request needs a conservative upper bound on what it can cost:
the input tokens it carries, the output cap it may use and, when the
model's price is known, the cost of both. This module computes that bound
without contacting a provider or a ledger; the Goal budget
(``models.usage.request``) and the Job resource governor
(``runtime.jobs.resource_governance``) both reserve against it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Any


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


__all__ = ["RequestReservation", "plan_request_reservation"]
