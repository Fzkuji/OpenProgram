"""Explicit Goal limits enforced through the public provider entry."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openprogram.models.providers.streaming.budget import QuotaExceeded
from openprogram.models.providers.streaming.stream import stream_simple_with_provider
from openprogram.models.providers.types import Context, Usage, SimpleStreamOptions, ModelCost
from tests.component.agent.test_goal_request_usage import bound, final, model
from tests.component.agent import test_goal_request_usage as _test_goal_request_usage

# Shared pytest fixtures, bound at module level so pytest collects them here.
runtime = _test_goal_request_usage.runtime


def test_goal_budget_refuses_before_credentials_and_provider(runtime):
    goals, chat, meter, goal = runtime
    goal["budget"]["max_cost_usd"] = 0.000001
    goals.save_goal("goal-usage", goal)
    calls = []

    class Provider:
        requires_credentials = True

        async def stream_simple(self, m, context, options):
            calls.append("provider")
            yield final(m, Usage(input=100, output=20))

    async def drain():
        async for _ in stream_simple_with_provider(
            Provider(), model(), Context(), get_api_key=lambda _: calls.append("credentials") or "test",
        ):
            pass

    with bound(chat, goal), pytest.raises(QuotaExceeded):
        asyncio.run(drain())
    assert calls == []
    assert goals.load_goal("goal-usage")["usage"]["requests"] == 0


@pytest.fixture
def transport(monkeypatch):
    """Keep actual builtin admission/adapter; substitute only its SDK transport."""
    from openprogram.models.providers.catalog.api_registry import get_api_provider, has_audited_accounting
    from openprogram.models.providers.adapters.openai_completions import openai_completions
    from tests.component.providers.adapters.test_completions_thinking_retry import _Stream
    provider = get_api_provider("openai-completions")
    assert has_audited_accounting(provider, "openai-completions")
    calls = []
    gate = {}

    class Completions:
        async def create(self, **params):
            calls.append(params)
            if gate:
                gate["entered"].set()
                await gate["release"].wait()
            output = min(params.get("max_tokens", params.get("max_completion_tokens", 20)), 20)
            usage = SimpleNamespace(prompt_tokens=30, completion_tokens=output, total_tokens=30 + output)
            return _Stream([SimpleNamespace(usage=usage, choices=[])])

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(openai_completions._openai, "AsyncOpenAI", Client)
    return provider, calls, gate


async def consume(provider, m, *, get_api_key=None, options=None):
    return [event async for event in stream_simple_with_provider(
        provider, m, Context(), options or SimpleStreamOptions(max_tokens=20, api_key="test"), get_api_key)]


def bounded_model():
    return model().model_copy(update={"provider": "openai", "context_window": 100, "max_tokens": 20})


def test_actual_provider_output_is_clamped_by_goal_token_allowance(runtime, transport):
    goals, chat, meter, goal = runtime
    provider, calls, _ = transport
    goal["budget"]["max_tokens"] = 110
    goals.save_goal("goal-usage", goal)
    with bound(chat, goal):
        asyncio.run(consume(provider, bounded_model()))
    assert calls[0]["max_tokens"] == 10
    assert goals.load_goal("goal-usage")["usage"]["total_tokens"] == 40


def test_parallel_request_reservations_share_goal_allowance(runtime, transport):
    goals, chat, meter, goal = runtime
    provider, calls, gate = transport
    goal["budget"]["max_cost_usd"] = 0.0004
    goals.save_goal("goal-usage", goal)
    keys = []

    async def run():
        gate.update(entered=asyncio.Event(), release=asyncio.Event())
        first = asyncio.create_task(consume(provider, bounded_model()))
        try:
            await asyncio.wait_for(gate["entered"].wait(), 30)
            with pytest.raises(QuotaExceeded, match="quota.cost_exhausted"):
                await consume(provider, bounded_model(), get_api_key=lambda _: keys.append("key") or "test")
        finally:
            gate["release"].set()
            await first

    with bound(chat, goal):
        asyncio.run(run())
    assert keys == [] and len(calls) == 1
    assert goals.load_goal("goal-usage")["usage"]["requests"] == 1
    assert goals.load_goal("goal-usage")["usage"]["total_tokens"] == 50


@pytest.mark.parametrize("change", ["lower", "add", "credentials_fail"])
def test_credentials_boundary_rechecks_budget_and_releases_unstarted(runtime, transport, change):
    goals, chat, meter, goal = runtime
    provider, calls, _ = transport
    if change != "add":
        goal["budget"]["max_tokens"] = 200
        goals.save_goal("goal-usage", goal)

    def credential(_):
        if change == "credentials_fail":
            raise RuntimeError("credential failed")
        goals.apply_goal_action("goal-usage", "budget", max_tokens=50)
        return "test"

    with bound(chat, goal), pytest.raises((QuotaExceeded, RuntimeError)):
        asyncio.run(consume(provider, bounded_model(), get_api_key=credential))
    assert calls == []
    with meter.read() as conn:
        assert [row[0] for row in conn.execute("SELECT state FROM usage_requests")] == ["released"]


@pytest.mark.parametrize("unknown", ["price", "tier", "prior_receipt"])
def test_hard_cost_budget_never_treats_unknown_as_free(runtime, transport, unknown):
    from openprogram.models.usage.request import RequestReceipt
    goals, chat, meter, goal = runtime
    provider, calls, _ = transport
    m = bounded_model()
    opts = SimpleStreamOptions(max_tokens=20, api_key="test")
    if unknown == "prior_receipt":
        with bound(chat, goal):
            receipt = RequestReceipt.begin(m, opts)
            receipt.start()
    elif unknown == "price":
        m.cost = ModelCost()
    else:
        opts.service_tier = "priority"
    goal = goals.load_goal("goal-usage")
    goal["budget"]["max_cost_usd"] = 1
    goals.save_goal("goal-usage", goal)
    with bound(chat, goal), pytest.raises(QuotaExceeded):
        asyncio.run(consume(provider, m, options=opts))
    assert calls == []


def test_expired_prepared_goal_request_cannot_dispatch(runtime, transport):
    import json
    from openprogram.models.usage.request import RequestReceipt
    goals, chat, meter, goal = runtime
    provider, _, _ = transport
    goal["budget"]["max_tokens"] = 200
    goals.save_goal("goal-usage", goal)
    with bound(chat, goal):
        receipt = RequestReceipt.begin(bounded_model(), SimpleStreamOptions(max_tokens=20),
                                       provider_context=Context(), provider=provider)
    receipt.metadata["reservation"]["expires_at"] = 1
    with meter.immediate() as conn:
        conn.execute("UPDATE usage_requests SET metadata_json=? WHERE request_id=?",
                     (json.dumps(receipt.metadata), receipt.request_id))
    meter.close()
    with pytest.raises(QuotaExceeded, match="expired"):
        RequestReceipt.load(meter, receipt.request_id).start()
    goal = chat.resume("goal-usage")
    with bound(chat, goal):
        replacement = RequestReceipt.begin(bounded_model(), SimpleStreamOptions(max_tokens=20),
                                           provider_context=Context(), provider=provider)
        replacement.start()
    with meter.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_requests WHERE state='started'").fetchone()[0] == 1


def joint_receipt(runtime, transport):
    from openprogram.runtime.jobs.types import Job
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.models.providers.streaming.budget import BudgetedRequest
    from openprogram.models.usage.request import RequestReceipt
    _, chat, meter, goal = runtime
    governor = ResourceGovernor(meter)
    job = Job(id="joint-job", parent_session_id="goal-usage", prompt="work", agent_id="main")
    assert governor.admit_job(job, persist=lambda _: None).accepted
    plan = governor.reserve_provider_request(job.id, input_token_upper_bound=100,
                                             requested_max_output_tokens=20, model=bounded_model())
    assert plan.allowed
    with bound(chat, goal):
        receipt = RequestReceipt.begin(bounded_model(), SimpleStreamOptions(max_tokens=20),
                                       provider_context=Context(), provider=transport[0],
                                       budget=BudgetedRequest(job.id, governor, plan))
    return receipt, governor


@pytest.mark.parametrize("kind", ["complete", "tokens_only", "cost_only", "components_only"])
def test_joint_receipt_releases_only_known_budget_kinds_after_reopen(runtime, transport, kind):
    from openprogram.models.usage.request import RequestReceipt
    goals, _, meter, _ = runtime
    receipt, governor = joint_receipt(runtime, transport)
    receipt.start()
    counter = {
        "complete": Usage(input=30, output=20),
        "tokens_only": Usage(total_tokens=50, tokens_reported=False),
        "cost_only": Usage(provider_cost_usd=0.00007, tokens_reported=False),
        "components_only": Usage(input=30, tokens_reported=False),
    }[kind]
    message = final(bounded_model(), counter).message
    receipt.settle(message)
    meter.close()
    restored = RequestReceipt.load(meter, receipt.request_id)
    restored.settle(message)
    restored.flush()
    assert governor.recover_provider_reservations(now=10**12) == 0
    with meter.read() as conn:
        states = {row["kind"]: row["state"] for row in meter.run_tables().handle.rows("usage_reservations")}
        assert states["token"] == ("settled" if kind in {"complete", "tokens_only"} else "started")
        assert states["cost"] == ("settled" if kind in {"complete", "cost_only"} else "started")
        assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1
    usage = goals.load_goal("goal-usage")["usage"]
    assert usage["total_tokens"] == (0 if kind == "cost_only" else 30 if kind == "components_only" else 50)
    assert usage["tokens_known"] == (kind in {"complete", "tokens_only"})
    assert usage["cost_known"] == (kind in {"complete", "cost_only"})


def test_expired_joint_job_reservation_cannot_start_goal_request(runtime, transport):
    _, _, meter, _ = runtime
    receipt, _ = joint_receipt(runtime, transport)
    with meter.run_tables().transaction() as conn:
        conn.update("usage_reservations", {"expires_at": 1})
    meter.close()
    with pytest.raises((QuotaExceeded, RuntimeError), match="expired"):
        receipt.start()
    with meter.read() as conn:
        assert conn.execute("SELECT state FROM usage_requests").fetchone()[0] == "prepared"
        assert {row["state"] for row in meter.run_tables().handle.rows("usage_reservations")} == {"reserved"}
