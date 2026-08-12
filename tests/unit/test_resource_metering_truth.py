from __future__ import annotations

from openprogram.agent.resource_governance import plan_request_reservation
from openprogram.providers.types import Model, ModelCost
from openprogram.usage import recorder


class _Usage:
    input = 10
    output = 5
    cache_read = 0
    cache_write = 0
    cost = None


class _Message:
    usage = _Usage()


def _model(cost: ModelCost) -> Model:
    return Model(
        id="m", name="M", api="openai-completions", provider="community",
        base_url="https://example.invalid", cost=cost, max_tokens=100,
    )


def test_zero_catalog_price_is_known_and_unknown_price_is_not(monkeypatch) -> None:
    known_zero = _model(ModelCost(input=0, output=0, cache_read=0, cache_write=0,
                                  source="model_catalog"))
    unknown = _model(ModelCost())

    known_cost, known_source = recorder._cost_from_model(known_zero, _Usage())
    unknown_cost, unknown_source = recorder._cost_from_model(unknown, _Usage())

    assert known_source == "model_catalog"
    assert known_cost["cost_total"] == 0.0
    assert unknown_source == "unknown"
    assert unknown_cost["cost_total"] == 0.0


def test_request_reservation_uses_safe_input_bound_and_clamps_output() -> None:
    plan = plan_request_reservation(
        input_token_upper_bound=120,
        requested_max_output_tokens=100,
        remaining_token_budget=150,
        model=_model(ModelCost(input=2.0, output=4.0, cache_read=0, cache_write=0,
                               source="model_catalog")),
    )

    assert plan.input_token_upper_bound == 120
    assert plan.output_token_cap == 30
    assert plan.token_reservation == 150
    assert plan.cost_known is True
    assert plan.cost_reservation_microusd == 360


def test_request_reservation_fails_closed_for_unknown_price_with_cost_budget() -> None:
    plan = plan_request_reservation(
        input_token_upper_bound=10,
        requested_max_output_tokens=20,
        remaining_token_budget=None,
        model=_model(ModelCost()),
        cost_budget_configured=True,
    )

    assert plan.allowed is False
    assert plan.reason_code == "quota.cost_unavailable"


def test_legacy_complete_price_without_source_is_known_but_partial_is_unknown() -> None:
    assert ModelCost(input=1, output=2, cache_read=0, cache_write=0).is_known()
    assert not ModelCost(input=1, output=2, cache_read=None, cache_write=0).is_known()


def test_request_reservation_rejects_zero_output_and_rounds_cost_up() -> None:
    priced = _model(ModelCost(input=0.1, output=0.1, cache_read=0, cache_write=0,
                              source="model_catalog"))
    empty = plan_request_reservation(input_token_upper_bound=10, requested_max_output_tokens=9,
                                     remaining_token_budget=10, model=priced)
    assert empty.allowed is False
    assert empty.reason_code == "quota.token_exhausted"
    plan = plan_request_reservation(input_token_upper_bound=1, requested_max_output_tokens=1,
                                    remaining_token_budget=None, model=priced)
    assert plan.cost_reservation_microusd == 1
