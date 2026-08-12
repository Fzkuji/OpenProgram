"""End-to-end budget enforcement at the provider stream chokepoint.

The governor primitives are unit-tested in test_resource_governance.py. What
these pin is the wiring: that a governed task's provider call actually
reserves before credentials, clamps its output cap, settles real usage
atomically, and fails closed when accounting is unavailable.
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.agent.resource_governance import (
    ResourceGovernor,
    ResourceLimits,
    resolve_resource_limits,
)
from openprogram.agent.task.types import Task
from openprogram.providers.budget import (
    QuotaExceeded,
    provider_retry_attempts,
    provider_sdk_retries,
)
from openprogram.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventStart,
    Model,
    ModelCost,
    SimpleStreamOptions,
    StreamOptions,
    Usage,
)
from openprogram.providers.structured_output import JsonSchemaOutput
from openprogram.usage import context as _ctx_mod
from openprogram.usage import recorder as _recorder
from openprogram.usage.context import UsageContext
from openprogram.usage.ledger import UsageLedger


@pytest.fixture(autouse=True)
def reset_usage_context():
    token = _ctx_mod._current.set(UsageContext())
    try:
        yield
    finally:
        _ctx_mod._current.reset(token)


class _FakeProvider:
    """Records the options it was handed so the clamp can be asserted."""

    requires_credentials = True

    def __init__(self, model, usage=None, fail_before_start=None):
        self._model = model
        self._usage = usage or Usage(input=100, output=20, cache_read=0, cache_write=0)
        self._fail_before_start = fail_before_start
        self.seen_opts = []

    async def stream_simple(self, model, context, opts):
        self.seen_opts.append(opts)
        if self._fail_before_start is not None:
            raise self._fail_before_start
        partial = AssistantMessage(
            role="assistant", content=[], api=model.api,
            provider=model.provider, model=model.id, timestamp=0,
        )
        yield EventStart(type="start", partial=partial)
        final = AssistantMessage(
            role="assistant", content=[], api=model.api, provider=model.provider,
            model=model.id, timestamp=0, usage=self._usage,
        )
        yield EventDone(type="done", reason="stop", message=final)

    async def stream(self, model, context, opts):
        async for event in self.stream_simple(model, context, opts):
            yield event


class _ConstructionFailureProvider:
    requires_credentials = False

    def stream_simple(self, model, context, opts):
        raise RuntimeError("generator construction failed")

    def stream(self, model, context, opts):
        raise RuntimeError("generator construction failed")


def _model(**kw):
    context_window = kw.pop("context_window", 1_000)
    return Model(
        id="fake-model-1", provider="fakeprov", api="openai-completions",
        name="fake", base_url="http://fake.local",
        context_window=context_window, **kw,
    )


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A governed live task whose provider calls run through stream.py."""
    import importlib
    stream_mod = importlib.import_module("openprogram.providers.stream")

    ledger = UsageLedger(tmp_path / "usage.db")
    monkeypatch.setattr(_recorder, "default_ledger", ledger)
    key_calls: list[str] = []
    monkeypatch.setattr(
        stream_mod, "resolve_provider_key",
        lambda provider: key_calls.append(provider) or "fake-key",
    )

    def build(*, limits=None, model=None, usage=None, fail_before_start=None):
        resolved = resolve_resource_limits(
            limits or ResourceLimits(), scheduler_capacity=1,
        )
        governor = ResourceGovernor(
            ledger, limit_resolver=lambda _sid, _task: resolved,
            session_limit_resolver=lambda _sid: resolved,
        )
        task = Task(id="t_budget", parent_session_id="s1", prompt="p", agent_id="a")
        assert governor.admit_task(task, persist=lambda _t: None).accepted
        used_model = model or _model()
        provider = _FakeProvider(
            used_model, usage=usage, fail_before_start=fail_before_start,
        )
        monkeypatch.setattr(
            stream_mod, "get_api_provider",
            lambda api: provider if api == used_model.api else None,
        )
        runner = type("R", (), {"_governor": governor})()
        monkeypatch.setattr(
            "openprogram.agent.task.runner.current_task_resource_context",
            lambda: (task.id, governor),
        )
        return {
            "governor": governor, "task": task, "model": used_model,
            "provider": provider, "ledger": ledger, "key_calls": key_calls,
            "runner": runner,
        }

    return build


def _drain(model, opts):
    return _drain_entry("stream_simple", model, opts)


def _drain_entry(entry, model, opts):
    from openprogram.providers import stream, stream_simple

    stream_fn = stream_simple if entry == "stream_simple" else stream

    async def go():
        events = []
        async for event in stream_fn(
            model, Context(system_prompt="sys", messages=[], tools=[]), opts,
        ):
            events.append(event)
        return events

    return asyncio.run(go())


def _reservation_states(ledger):
    return sorted(
        row[0] for row in ledger.connection().execute(
            "SELECT state FROM usage_reservations"
        )
    )


def test_budgeted_call_settles_actual_usage_once(wired):
    """A governed call records provider-authoritative usage, not the estimate."""
    env = wired(limits=ResourceLimits(max_total_tokens=100_000))
    _drain(env["model"], SimpleStreamOptions(session_id="s1"))

    rows = env["ledger"].query(group_by=["model_id"])
    assert len(rows) == 1
    assert (rows[0].input_tokens, rows[0].output_tokens) == (100, 20)
    assert rows[0].events == 1
    # Both the token and cost legs settle; no exposure is left reserved.
    assert set(_reservation_states(env["ledger"])) == {"settled"}


def test_actual_usage_cannot_exceed_reserved_token_exposure(wired):
    env = wired(
        limits=ResourceLimits(max_total_tokens=100_000),
        usage=Usage(input=90_000, output=20_000, cache_read=0, cache_write=0),
    )

    with pytest.raises(QuotaExceeded, match="actual usage exceeds reservation"):
        _drain(env["model"], SimpleStreamOptions(session_id="s1"))

    assert set(_reservation_states(env["ledger"])) == {"started"}


def test_budgeted_provider_disables_adapter_internal_retries(wired):
    wired(limits=ResourceLimits(max_total_tokens=100_000))

    assert provider_sdk_retries(3) == 0
    assert provider_retry_attempts(5) == 1


def test_small_request_remains_usable_under_strict_budget(wired):
    env = wired(limits=ResourceLimits(max_total_tokens=2_000))
    _drain(env["model"], SimpleStreamOptions(session_id="s1", max_tokens=100))

    assert env["provider"].seen_opts


def test_structured_output_schema_is_included_in_safe_input_bound(wired):
    env = wired(
        limits=ResourceLimits(max_total_tokens=3_000),
        model=_model(context_window=100_000),
    )
    schema = JsonSchemaOutput(schema={
        "type": "object",
        "properties": {"answer": {"type": "string", "description": "x" * 4_000}},
    })

    with pytest.raises(QuotaExceeded):
        _drain(
            env["model"],
            SimpleStreamOptions(session_id="s1", max_tokens=100, response_format=schema),
        )


def test_budgeted_payload_mutator_fails_closed_before_provider(wired):
    env = wired(limits=ResourceLimits(max_total_tokens=100_000))

    with pytest.raises(QuotaExceeded, match="on_payload"):
        _drain(
            env["model"],
            SimpleStreamOptions(session_id="s1", on_payload=lambda payload, model: payload),
        )
    assert env["provider"].seen_opts == []


def test_output_cap_is_clamped_to_remaining_budget(wired):
    """The provider cannot be asked for more output than the budget allows."""
    env = wired(
        limits=ResourceLimits(max_total_tokens=1_500),
        model=_model(max_tokens=100_000, context_window=100_000),
    )
    _drain(env["model"], SimpleStreamOptions(session_id="s1", max_tokens=50_000))

    seen = env["provider"].seen_opts[0]
    assert seen.max_tokens is not None
    assert seen.max_tokens <= 1_500
    # And it never exceeds what the input bound leaves behind.
    assert seen.max_tokens < 50_000


def test_reasoning_budget_is_reserved_and_left_room_for(wired):
    """Anthropic raises max_tokens by the thinking budget when the declared
    cap is lower, so the reservation must cover reasoning and the cap handed
    to the provider must leave room for it."""
    from openprogram.providers.budget import requested_output_cap

    opts = SimpleStreamOptions(session_id="s1", max_tokens=1_000, reasoning="high")
    model = _model(max_tokens=100_000)
    assert requested_output_cap(opts, model) > 1_000

    env = wired(limits=ResourceLimits(max_total_tokens=100_000), model=model)
    _drain(env["model"], opts)
    seen = env["provider"].seen_opts[0]
    # Declared cap plus the reasoning the provider adds stays inside what
    # was actually reserved.
    assert seen.max_tokens + 8192 <= 100_000


def test_exhausted_token_budget_refuses_before_credentials(wired):
    """A denied call resolves no key and opens no connection."""
    env = wired(limits=ResourceLimits(max_total_tokens=1))
    with pytest.raises(QuotaExceeded) as excinfo:
        _drain(env["model"], SimpleStreamOptions(session_id="s1"))

    assert excinfo.value.reason_code == "quota.token_exhausted"
    assert excinfo.value.retryable is False
    assert env["key_calls"] == []
    assert env["provider"].seen_opts == []


def test_cost_budget_with_unknown_price_fails_closed(wired):
    """Unknown price is not zero: a configured cost budget refuses the call."""
    env = wired(
        limits=ResourceLimits(max_cost_usd="1.00"),
        model=_model(cost=ModelCost()),  # source="unknown"
    )
    with pytest.raises(QuotaExceeded) as excinfo:
        _drain(env["model"], SimpleStreamOptions(session_id="s1"))

    assert excinfo.value.reason_code == "quota.cost_unavailable"
    assert env["key_calls"] == []


def test_accounting_outage_fails_closed_and_is_retryable(wired, monkeypatch):
    """A ledger that cannot answer must not let a budgeted call run unmetered."""
    env = wired(limits=ResourceLimits(max_total_tokens=100_000))
    monkeypatch.setattr(
        env["governor"], "reserve_provider_request",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down")),
    )
    with pytest.raises(QuotaExceeded) as excinfo:
        _drain(env["model"], SimpleStreamOptions(session_id="s1"))

    assert excinfo.value.reason_code == "quota.accounting_unavailable"
    assert excinfo.value.retryable is True
    assert env["provider"].seen_opts == []


def test_settlement_failure_surfaces_as_accounting_error(wired, monkeypatch):
    """A budgeted call must not swallow a settle failure the way the
    best-effort recorder does — otherwise usage silently goes unbilled."""
    env = wired(limits=ResourceLimits(max_total_tokens=100_000))
    monkeypatch.setattr(
        env["ledger"], "append_in_transaction",
        lambda _conn, _event: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with pytest.raises(QuotaExceeded) as excinfo:
        _drain(env["model"], SimpleStreamOptions(session_id="s1"))

    assert excinfo.value.reason_code == "quota.accounting_unavailable"
    # Exposure stays held rather than being released on a failed settle.
    assert "released" not in _reservation_states(env["ledger"])


@pytest.mark.parametrize("entry", ["stream_simple", "stream"])
def test_credential_failure_releases_reserved_exposure(
    wired, monkeypatch, entry,
):
    """A pre-I/O failure must not consume a governed task's budget."""
    import importlib

    env = wired(limits=ResourceLimits(max_total_tokens=100_000))
    stream_mod = importlib.import_module("openprogram.providers.stream")
    monkeypatch.setattr(
        stream_mod, "resolve_provider_key",
        lambda _provider: (_ for _ in ()).throw(RuntimeError("credential read failed")),
    )

    opts = SimpleStreamOptions(session_id="s1") if entry == "stream_simple" else StreamOptions(session_id="s1")
    with pytest.raises(RuntimeError, match="credential read failed"):
        _drain_entry(entry, env["model"], opts)

    assert set(_reservation_states(env["ledger"])) == {"released"}
    assert env["provider"].seen_opts == []


@pytest.mark.parametrize("entry", ["stream_simple", "stream"])
def test_generator_construction_failure_releases_reserved_exposure(
    wired, monkeypatch, entry,
):
    """A provider failure before its async iterator exists is pre-I/O."""
    import importlib

    env = wired(limits=ResourceLimits(max_total_tokens=100_000))
    stream_mod = importlib.import_module("openprogram.providers.stream")
    monkeypatch.setattr(
        stream_mod, "get_api_provider", lambda _api: _ConstructionFailureProvider(),
    )
    opts = (
        SimpleStreamOptions(session_id="s1")
        if entry == "stream_simple"
        else StreamOptions(session_id="s1")
    )

    with pytest.raises(RuntimeError, match="generator construction failed"):
        _drain_entry(entry, env["model"], opts)

    assert set(_reservation_states(env["ledger"])) == {"released"}


@pytest.mark.parametrize("entry", ["stream_simple", "stream"])
@pytest.mark.parametrize("failure", [RuntimeError("transport failed"), asyncio.CancelledError()])
def test_failure_after_provider_start_keeps_conservative_exposure(
    wired, entry, failure,
):
    """Once provider I/O starts, no event is required to retain exposure."""
    env = wired(
        limits=ResourceLimits(max_total_tokens=100_000),
        fail_before_start=failure,
    )
    opts = SimpleStreamOptions(session_id="s1") if entry == "stream_simple" else StreamOptions(session_id="s1")
    with pytest.raises(type(failure)):
        _drain_entry(entry, env["model"], opts)

    assert set(_reservation_states(env["ledger"])) == {"started"}


def test_missing_final_usage_keeps_conservative_exposure(wired):
    """A request that reached the provider but reported no usage keeps its
    reservation: releasing it would under-count real spend."""
    env = wired(
        limits=ResourceLimits(max_total_tokens=100_000),
        usage=Usage(input=0, output=0, cache_read=0, cache_write=0),
    )
    _drain(env["model"], SimpleStreamOptions(session_id="s1"))

    assert set(_reservation_states(env["ledger"])) == {"started"}
    assert env["ledger"].query()[0].events == 0


def test_crash_before_settle_leaves_started_exposure_for_recovery(wired):
    """Fault injection at the reserve/settle boundary: a worker that dies
    after provider I/O must not have its exposure reclaimed by expiry, since
    the request really may have billed."""
    env = wired(limits=ResourceLimits(max_total_tokens=100_000))
    governor, ledger = env["governor"], env["ledger"]
    reservation = governor.reserve_provider_request(
        env["task"].id, input_token_upper_bound=10,
        requested_max_output_tokens=20, model=env["model"],
    )
    governor.start_provider_request(reservation.reservation_id)
    ledger.connection().execute(
        "UPDATE usage_reservations SET expires_at = 0 WHERE reservation_id LIKE ?",
        (reservation.reservation_id + ":%",),
    )
    ledger.connection().commit()

    # Expiry reclaims only never-started requests.
    assert governor.recover_provider_reservations(now=1) == 0
    assert set(_reservation_states(ledger)) == {"started"}


def test_concurrent_budgeted_calls_share_one_task_budget(wired):
    """Siblings racing on the same scope cannot jointly exceed the ceiling."""
    env = wired(limits=ResourceLimits(max_total_tokens=400))
    governor, model = env["governor"], env["model"]

    accepted, refused = [], []
    for _ in range(6):
        plan = governor.reserve_provider_request(
            env["task"].id, input_token_upper_bound=100,
            requested_max_output_tokens=50, model=model,
        )
        (accepted if plan.allowed else refused).append(plan)

    assert accepted, "at least one call must fit the budget"
    assert refused, "the budget must stop the rest"
    reserved = env["ledger"].connection().execute(
        "SELECT COALESCE(SUM(reserved_tokens), 0) FROM usage_reservations "
        "WHERE kind = 'token' AND state IN ('reserved','started')"
    ).fetchone()[0]
    assert reserved <= 400
