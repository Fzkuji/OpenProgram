"""Request attribution through the real provider stream and durable Goal state."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from openprogram.models.providers.streaming.stream import stream_simple_with_provider
from openprogram.models.providers.types import AssistantMessage, Context, EventDone, Model, ModelCost, Usage
from openprogram.models.usage import context as usage_context
from openprogram.models.usage.context import UsageContext, usage_scope
from openprogram.models.usage.ledger import UsageLedger


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.models.usage import ledger, recorder

    db = SessionDB(tmp_path / "sessions")
    db.create_session("goal-usage", "main")
    meter = UsageLedger(tmp_path / "usage.db")
    monkeypatch.setattr(goals, "_db", lambda: db)
    monkeypatch.setattr(goals, "_emit_goal_update", lambda *a, **k: None)
    monkeypatch.setattr(ledger, "default_ledger", meter)
    monkeypatch.setattr(recorder, "default_ledger", meter)
    token = usage_context._current.set(UsageContext())
    goal = chat.create("goal-usage", "finish and verify")
    try:
        yield goals, chat, meter, goal
    finally:
        usage_context._current.reset(token)
        meter.close()
        db.close()


def model():
    return Model(id="meter-test", name="test", api="openai-completions",
                 provider="test", base_url="http://invalid.test",
                 cost=ModelCost(input=1, output=2, cache_read=1, cache_write=1, source="configured"))


def final(m, usage):
    return EventDone(reason="stop", message=AssistantMessage(
        content=[], api=m.api, provider=m.provider, model=m.id, timestamp=0, usage=usage))


def run(provider, m=None):
    async def drain():
        async for _ in stream_simple_with_provider(provider, m or model(), Context(messages=[])):
            pass
    asyncio.run(drain())


@contextmanager
def bound(chat, goal):
    token = chat._turn_goal.set(chat.identity(goal))
    try:
        with usage_scope(session_id="goal-usage", call_kind="chat"):
            yield
    finally:
        chat._turn_goal.reset(token)


def test_paused_goal_receives_late_provider_usage(runtime):
    goals, chat, meter, goal = runtime

    class Provider:
        requires_credentials = False

        async def stream_simple(self, m, context, options):
            goals.apply_goal_action("goal-usage", "pause")
            yield final(m, Usage(input=100, output=20))

    token = chat._turn_goal.set(chat.identity(goal))
    try:
        with usage_scope(session_id="goal-usage", call_kind="chat"):
            run(Provider())
    finally:
        chat._turn_goal.reset(token)
    saved = goals.load_goal("goal-usage")
    assert saved["status"] == "paused"
    assert saved["usage"]["total_tokens"] == 120
    assert saved["usage"]["cost_usd"] == pytest.approx(0.00014)
    assert meter.query()[0].events == 1


@pytest.mark.parametrize("outcome", ["missing", "serialized_missing", "exception", "zero", "unknown_price", "tier"])
def test_request_unknown_is_not_free(runtime, outcome):
    goals, chat, meter, goal = runtime
    m = model()
    if outcome == "unknown_price":
        m.cost = ModelCost()

    class Provider:
        requires_credentials = False

        async def stream_simple(self, m, context, options):
            if outcome == "exception":
                raise RuntimeError("lost stream")
            usage = (Usage() if outcome in {"missing", "serialized_missing"} else Usage(input=0, output=0) if outcome == "zero"
                     else Usage(input=100, output=20, requested_service_tier="priority" if outcome == "tier" else None))
            event = final(m, usage)
            yield event.model_dump() if outcome == "serialized_missing" else event

    with bound(chat, goal):
        if outcome == "exception":
            with pytest.raises(RuntimeError, match="lost stream"):
                run(Provider(), m)
        else:
            run(Provider(), m)
    chat.refresh_usage("goal-usage")
    usage = goals.load_goal("goal-usage")["usage"]
    assert usage["requests"] == 1
    assert usage["tokens_known"] == (outcome not in {"missing", "serialized_missing", "exception"})
    assert usage["cost_known"] == (outcome == "zero")
    assert usage["unknown_cost_requests"] == (0 if outcome == "zero" else 1)


@pytest.mark.parametrize("change", ["pause", "edit", "replacement"])
def test_receipt_reopens_with_original_identity_and_price(runtime, change):
    from openprogram.models.usage.request import RequestReceipt
    from openprogram.models.providers.types import SimpleStreamOptions
    goals, chat, meter, goal = runtime
    m = model()
    with bound(chat, goal):
        request = RequestReceipt.begin(m, SimpleStreamOptions())
        request.start()
    if change == "replacement":
        goals.apply_goal_action("goal-usage", "clear")
        chat.create("goal-usage", "unrelated goal")
    else:
        goals.apply_goal_action("goal-usage", change, prompt="new revision")
    m.cost.input = 100
    m.cost.output = 100
    meter.close()
    loaded = RequestReceipt.load(meter, request.request_id)
    message = final(m, Usage(input=100, output=20)).message
    loaded.settle(message)
    loaded.settle(message)
    assert meter.query()[0].events == 1
    original = meter.goal_usage("goal-usage", goal["goal_id"])
    assert original["total_tokens"] == 120
    assert original["cost_usd"] == pytest.approx(0.00014)
    saved = goals.load_goal("goal-usage")
    assert saved["usage"]["total_tokens"] == (0 if change == "replacement" else 120)
    with meter.read() as conn:
        row = conn.execute("SELECT goal_id, goal_revision, request_id FROM usage_events").fetchone()
    assert tuple(row) == (goal["goal_id"], 1, request.request_id)
    with pytest.raises(ValueError, match="Conflicting"):
        loaded.settle(final(m, Usage(input=1, output=1)).message)


def test_receipt_projection_replays_after_append_failure(runtime, monkeypatch):
    from openprogram.models.usage.request import RequestReceipt
    from openprogram.models.providers.types import SimpleStreamOptions
    goals, chat, meter, goal = runtime
    with bound(chat, goal):
        request = RequestReceipt.begin(model(), SimpleStreamOptions())
        request.start()
    append = meter.append_in_transaction
    def unavailable(*args):
        raise OSError("ledger unavailable")
    monkeypatch.setattr(meter, "append_in_transaction", unavailable)
    with pytest.raises(OSError):
        request.settle(final(model(), Usage(input=100, output=20)).message)
    monkeypatch.setattr(meter, "append_in_transaction", append)
    meter.close()
    goals.apply_goal_action("goal-usage", "pause")
    chat.refresh_usage("goal-usage")
    chat.refresh_usage("goal-usage")
    assert goals.load_goal("goal-usage")["usage"]["total_tokens"] == 120
    assert meter.query()[0].events == 1


def test_unscoped_paused_chat_cannot_charge_old_goal(runtime):
    goals, chat, meter, goal = runtime
    goals.apply_goal_action("goal-usage", "pause")

    class Provider:
        requires_credentials = False

        async def stream_simple(self, m, context, options):
            yield final(m, Usage(input=100, output=20))

    with usage_scope(session_id="goal-usage", call_kind="chat"):
        run(Provider())
    assert meter.query()[0].total_tokens == 120
    assert goals.load_goal("goal-usage")["usage"]["total_tokens"] == 0


def test_request_persistence_failure_prevents_credentials_and_provider(runtime, monkeypatch):
    goals, chat, meter, goal = runtime
    calls = []
    @contextmanager
    def unavailable():
        raise OSError("cannot persist request")
        yield
    monkeypatch.setattr(meter, "immediate", unavailable)

    class Provider:
        async def stream_simple(self, *args):
            calls.append("provider")
            yield

    async def drive():
        async for _ in stream_simple_with_provider(Provider(), model(), Context(messages=[]),
                                                   get_api_key=lambda _: calls.append("credentials")):
            pass
    with bound(chat, goal), pytest.raises(OSError):
        asyncio.run(drive())
    assert calls == []


def test_inherited_context_snapshot_preserves_goal_and_revision(runtime):
    from openprogram.models.usage.context import apply_snapshot, snapshot
    from openprogram.models.usage.request import RequestReceipt
    from openprogram.models.providers.types import SimpleStreamOptions
    _, chat, meter, goal = runtime
    with bound(chat, goal):
        saved = snapshot()
    apply_snapshot(saved)
    with usage_scope(call_kind="exec", call_label="child"):
        request = RequestReceipt.begin(model(), SimpleStreamOptions())
        request.start()
        request.settle(final(model(), Usage(input=10, output=3)).message)
    assert meter.goal_usage("goal-usage", goal["goal_id"])["total_tokens"] == 13


@pytest.mark.parametrize("action", ["pause", "edit", "credential_failure"])
def test_controls_during_credentials_prevent_provider_start(runtime, action):
    from openprogram.models.providers.types import SimpleStreamOptions
    goals, chat, meter, goal = runtime
    calls = []

    class Provider:
        async def stream_simple(self, *args):
            calls.append("provider")
            yield

    def credential(_):
        if action == "credential_failure":
            raise RuntimeError("no credential")
        goals.apply_goal_action("goal-usage", action, prompt="new revision")
        return "test-key"

    async def drive():
        async for _ in stream_simple_with_provider(Provider(), model(), Context(messages=[]), SimpleStreamOptions(), credential):
            pass

    with bound(chat, goal), pytest.raises((goals.GoalConflictError, RuntimeError)):
        asyncio.run(drive())
    assert calls == []
    assert meter.goal_usage("goal-usage", goal["goal_id"])["requests"] == 0
    with meter.read() as conn:
        assert conn.execute("SELECT state FROM usage_requests").fetchone()[0] == "released"


def test_terminal_receipt_is_one_total_not_sum_of_stream_snapshots(runtime):
    from openprogram.models.providers.types import EventStart
    goals, chat, meter, goal = runtime
    class Provider:
        requires_credentials = False

        async def stream_simple(self, m, context, options):
            yield EventStart(partial=final(m, Usage(input=20, output=2, total_tokens=22)).message)
            event = final(m, Usage(input=20, output=5, cache_read=30, total_tokens=60))
            yield event
            yield event  # Duplicate delivery is not a second request.

    with bound(chat, goal):
        run(Provider())
    assert goals.load_goal("goal-usage")["usage"]["total_tokens"] == 60
    assert meter.query()[0].events == 1


def test_unknown_request_can_receive_late_receipt_after_reopen(runtime):
    from openprogram.models.usage.request import RequestReceipt
    goals, chat, meter, goal = runtime
    class Provider:
        requires_credentials = False

        async def stream_simple(self, m, context, options):
            yield final(m, Usage())

    with bound(chat, goal):
        run(Provider())
    with meter.read() as conn:
        request_id = conn.execute("SELECT request_id FROM usage_requests").fetchone()[0]
    assert goals.load_goal("goal-usage")["usage"]["unknown_token_requests"] == 1
    goals.apply_goal_action("goal-usage", "pause")
    meter.close()
    RequestReceipt.load(meter, request_id).settle(final(model(), Usage(input=10, output=3)).message)
    saved = goals.load_goal("goal-usage")
    assert saved["status"] == "paused"
    assert saved["usage"]["total_tokens"] == 13
    assert saved["usage"]["unknown_token_requests"] == 0


def test_legacy_totals_migrate_without_assigning_past_requests(runtime):
    from openprogram.models.usage.request import RequestReceipt
    from openprogram.models.providers.types import SimpleStreamOptions
    goals, chat, meter, goal = runtime
    goal.pop("usage_mode")
    goal["usage"].update(total_tokens=11, cost_usd=0.25)
    goals.reset_goal_usage_cursor("goal-usage", goal)
    goals.save_goal("goal-usage", goal)
    with bound(chat, goal):
        request = RequestReceipt.begin(model(), SimpleStreamOptions())
        request.start()
        request.settle(final(model(), Usage(input=10, output=3)).message)
    saved = goals.load_goal("goal-usage")
    assert saved["legacy_usage"]["total_tokens"] == 11
    assert saved["usage"]["total_tokens"] == 24
    assert meter.goal_usage("goal-usage", goal["goal_id"])["total_tokens"] == 13


@pytest.mark.parametrize("mid_turn", [False, True])
def test_canonical_turn_freezes_actual_execution_and_mid_turn_goal(runtime, tmp_path, monkeypatch, mid_turn):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.execution import ExecutionStore, AttemptStore
    from openprogram.runtime.execution.driver import DriverRegistry
    from openprogram.runtime.execution.control import RuntimeControlService
    goals, chat, meter, goal = runtime
    store = ExecutionStore(tmp_path / "executions.db")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: control)
    if mid_turn:
        goals.apply_goal_action("goal-usage", "clear")

    class Provider:
        requires_credentials = False

        def __init__(self, pause):
            self.pause = pause

        async def stream_simple(self, m, context, options):
            if self.pause:
                goals.apply_goal_action("goal-usage", "pause")
            yield final(m, Usage(input=10, output=3))

    def runner(*, request, cancel_event):
        with usage_scope(session_id=request.session_id, call_kind="chat"):
            if mid_turn:
                run(Provider(False))
                chat.create(request.session_id, "created during real execution")
            run(Provider(True))
        return SimpleNamespace(failed=False)

    adapter = CanonicalAgentAdapter(turn_runner=runner)
    admission = adapter.admit(TurnRequest("goal-usage", "work", "main", "web"),
                              trusted_actor={}, user_message_id="u1", config_snapshot_ref="session:goal-usage")
    asyncio.run(adapter.activate(admission))
    saved = goals.load_goal("goal-usage")
    assert saved["usage"]["total_tokens"] == 13
    assert saved["status"] == "paused"
    with meter.read() as conn:
        row = conn.execute("SELECT goal_id, execution_id FROM usage_events WHERE request_id IS NOT NULL").fetchone()
    assert tuple(row) == (saved["goal_id"], admission.execution_id)


@pytest.mark.parametrize("adapter", ["responses", "google", "bedrock", "gemini_cli"])
@pytest.mark.parametrize("reported", [False, True, "components"])
def test_real_adapter_zero_and_missing_usage_remain_distinct(runtime, monkeypatch, adapter, reported):
    goals, chat, meter, goal = runtime
    m = model()
    components = reported == "components"

    class Provider:
        requires_credentials = False

        async def stream_simple(self, m, context, options):
            if adapter == "responses":
                from openprogram.models.providers.shared.openai_responses import process_responses_stream
                output = AssistantMessage(content=[], api=m.api, provider=m.provider, model=m.id, timestamp=0)
                async def events():
                    response = {"status": "completed"}
                    if reported:
                        response["usage"] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                        if components:
                            response["usage"] = {"input_tokens": 30, "output_tokens": 60, "total_tokens": 90,
                                                 "input_tokens_details": {"cached_tokens": 10},
                                                 "output_tokens_details": {"reasoning_tokens": 40}}
                    yield {"type": "response.completed", "response": response}
                await process_responses_stream(events(), output, SimpleNamespace(push=lambda event: None), m)
                yield final(m, output.usage).model_dump()
            elif adapter == "bedrock":
                from openprogram.models.providers.adapters.amazon_bedrock import amazon_bedrock as bedrock
                output = {"usage": bedrock._new_usage()}
                counters = ({"inputTokens": 20, "outputTokens": 60, "totalTokens": 90, "cacheReadInputTokens": 10}
                            if components else {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0})
                bedrock._handle_metadata_bedrock(
                    {"usage": counters} if reported else {}, m, output)
                yield {"type": "done", "message": {**final(m, Usage()).message.model_dump(), "usage": output["usage"]}}
            elif adapter == "google":
                from google import genai
                from google.genai import types
                from openprogram.models.providers.adapters.google import google
                class Models:
                    async def generate_content_stream(self, **kwargs):
                        async def chunks():
                            yield types.GenerateContentResponse(candidates=[], usage_metadata=(
                                types.GenerateContentResponseUsageMetadata(
                                    prompt_token_count=30 if components else 0, candidates_token_count=20 if components else 0,
                                    thoughts_token_count=40 if components else 0, cached_content_token_count=10 if components else 0,
                                    total_token_count=90 if components else 0) if reported else None))
                        return chunks()
                monkeypatch.setattr(genai, "Client", lambda **kw: SimpleNamespace(aio=SimpleNamespace(models=Models())))
                from openprogram.models.providers.types import SimpleStreamOptions
                async for event in google.stream_simple(m, context, SimpleStreamOptions(api_key="test")):
                    yield event
            else:
                from openprogram.models.providers.adapters.google_gemini_cli import google_gemini_cli as cli
                class Response:
                    status_code = 200
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, *args):
                        return None
                    async def aiter_lines(self):
                        chunk = {"candidates": []}
                        if reported:
                            chunk["usageMetadata"] = {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0}
                            if components:
                                chunk["usageMetadata"] = {"promptTokenCount": 30, "candidatesTokenCount": 20, "totalTokenCount": 90,
                                                          "thoughtsTokenCount": 40, "cachedContentTokenCount": 10}
                        yield "data: " + json.dumps(chunk)
                class Client(Response):
                    def stream(self, *args, **kwargs):
                        return Response()
                monkeypatch.setattr(cli, "build_async_client", lambda **kwargs: Client())
                async for event in cli.stream_google_gemini_cli(m, context, {"api_key": "test", "project_id": "test"}):
                    yield event

    with bound(chat, goal):
        run(Provider(), m)
    usage = goals.load_goal("goal-usage")["usage"]
    assert usage["tokens_known"] is bool(reported)
    assert usage["cost_known"] is bool(reported)
    assert usage["total_tokens"] == (90 if components else 0)
    if components:
        assert usage["cost_usd"] == pytest.approx(0.00015)


def test_completions_bill_reasoning_and_cached_input_once(runtime):
    from openprogram.models.providers.adapters.openai_completions.openai_completions import _usage_from_chunk
    goals, chat, meter, goal = runtime
    class Provider:
        requires_credentials = False
        async def stream_simple(self, m, context, options):
            usage = _usage_from_chunk(SimpleNamespace(prompt_tokens=30, completion_tokens=60, total_tokens=90,
                prompt_tokens_details=SimpleNamespace(cached_tokens=10),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=40)))
            yield final(m, usage)
    with bound(chat, goal):
        run(Provider())
    usage = goals.load_goal("goal-usage")["usage"]
    assert usage["total_tokens"] == 90
    assert usage["cost_usd"] == pytest.approx(0.00015)


def test_provider_factory_failure_cannot_be_reported_as_no_request(runtime):
    goals, chat, meter, goal = runtime
    invoked = []
    class Provider:
        requires_credentials = False

        def stream_simple(self, *args):
            # A plugin can perform synchronous I/O before returning an iterator.
            invoked.append(True)
            raise RuntimeError("lost response before iterator returned")

    with bound(chat, goal), pytest.raises(RuntimeError, match="lost response"):
        run(Provider())
    assert invoked == [True]
    usage = goals.load_goal("goal-usage")["usage"]
    assert usage["requests"] == 1
    assert usage["unknown_token_requests"] == 1


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("complete_counters", [False, True])
def test_partial_cumulative_usage_survives_interruption_without_double_count(runtime, terminal, complete_counters):
    from openprogram.models.providers.types import EventStart
    goals, chat, meter, goal = runtime

    class Provider:
        requires_credentials = False

        async def stream_simple(self, m, context, options):
            for output in (10, 20, 20, 15):
                yield EventStart(partial=final(m, Usage(input=100, output=output, tokens_reported=complete_counters)).message).model_dump()
            if terminal:
                yield final(m, Usage(input=100, output=25))
            else:
                raise RuntimeError("stream interrupted")

    with bound(chat, goal):
        if terminal:
            run(Provider())
        else:
            with pytest.raises(RuntimeError, match="interrupted"):
                run(Provider())
    meter.close()
    usage = goals.load_goal("goal-usage")["usage"]
    assert usage["total_tokens"] == (125 if terminal else 120)
    assert usage["cost_usd"] == pytest.approx(0.00015 if terminal else 0.00014)
    assert usage["unknown_token_requests"] == (0 if terminal else 1)
    assert usage["unknown_cost_requests"] == (0 if terminal else 1)
    assert sum(row.events for row in meter.query()) == (1 if terminal else 0)
