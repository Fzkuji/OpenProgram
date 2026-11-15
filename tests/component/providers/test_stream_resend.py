"""A streamed request whose reply fails in transport is sent again.

A scripted provider stalls or drops its connection before its first event,
after reasoning, after text and after a tool call. Read through the one
place every provider's stream passes (``stream_simple_with_provider``), each
such request goes out again within the shared retry bound and answers; the
consumer sees one ``start``, a ``retry`` that voids what the failed attempt
streamed, and the final message of the attempt that got through. A request
that is not a transport failure, is governed by a reservation, was already
retried to the bound by its own adapter, or was stopped is not sent again. Every attempt
that reached the provider is in the usage ledger.
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.streaming import budget as budget_module
from openprogram.models.providers.streaming.stream import stream_simple_with_provider
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextStart,
    EventThinkingDelta,
    EventThinkingStart,
    EventToolCallEnd,
    EventToolCallStart,
    Model,
    ModelCost,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
    UserMessage,
)
from openprogram.models.providers.utils import stream_retry
from openprogram.models.providers.utils.errors import ExecInterrupt
from openprogram.models.providers.utils.recovery import RecoveryState, current_recovery
from openprogram.models.providers.utils.stream_idle import StreamIdleTimeout
from openprogram.models.providers.utils.stream_retry import ProviderStreamError

IDLE_S = 0.2
_API = "resend-test-api"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_STREAM_IDLE_TIMEOUT_S", str(IDLE_S))
    monkeypatch.setattr(stream_retry, "_BACKOFF_BASE_S", 0.01)


def _model(model_id: str = "resend-model") -> Model:
    return Model(id=model_id, name="Resend model", api=_API, provider="resend-provider",
                 base_url="http://resend.invalid",
                 cost=ModelCost(input=1.0, output=2.0, cache_read=0.1, cache_write=0.0,
                                source="configured"))


def _context() -> Context:
    return Context(system_prompt="", messages=[UserMessage(role="user", content="hi", timestamp=0)])


def _message(model: Model, content=(), usage: Usage | None = None) -> AssistantMessage:
    return AssistantMessage(content=list(content), api=model.api, provider=model.provider,
                            model=model.id, stop_reason="stop", timestamp=0,
                            usage=usage or Usage())


class Scripted:
    """Plays one script per call; the last script repeats.

    Steps: ``start``, ``thinking``, ``text``, ``tool``, ``usage`` (a partial
    that reports input tokens), ``stall`` (waits forever), ``done``, or an
    exception to raise.
    """

    def __init__(self, *scripts, bounds_own_stream_idle: bool = False) -> None:
        self.scripts = list(scripts)
        self.calls = 0
        self.closed = 0
        self.bounds_own_stream_idle = bounds_own_stream_idle

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        script = self.scripts[min(self.calls, len(self.scripts) - 1)]
        self.calls += 1
        try:
            for step in script:
                if isinstance(step, BaseException):
                    raise step
                if step == "stall":
                    await asyncio.Event().wait()
                elif step == "start":
                    yield EventStart(partial=_message(model))
                elif step == "usage":
                    yield EventStart(partial=_message(model, usage=Usage(input=40, total_tokens=40)))
                elif step == "thinking":
                    content = [ThinkingContent(thinking="hmm")]
                    yield EventThinkingStart(content_index=0, partial=_message(model, content))
                    yield EventThinkingDelta(content_index=0, delta="hmm", partial=_message(model, content))
                elif step == "text":
                    content = [TextContent(text="I will")]
                    yield EventTextStart(content_index=0, partial=_message(model, content))
                    yield EventTextDelta(content_index=0, delta="I will", partial=_message(model, content))
                elif step == "tool":
                    call = ToolCall(id="call_1", name="lookup", arguments={"q": "x"})
                    yield EventToolCallStart(content_index=0, partial=_message(model, [call]))
                    yield EventToolCallEnd(content_index=0, tool_call=call, partial=_message(model, [call]))
                elif step == "done":
                    yield EventDone(reason="stop", message=_message(
                        model, [TextContent(text="answer")], Usage(input=10, output=2, total_tokens=12)))
        finally:
            self.closed += 1


async def _read(provider, options: SimpleStreamOptions | None = None, state: RecoveryState | None = None,
                model_id: str = "resend-model"):
    if state is not None:
        current_recovery.set(state)
    events = []
    try:
        async for event in stream_simple_with_provider(
            provider, _model(model_id), _context(), options or SimpleStreamOptions(api_key="k"),
        ):
            events.append(event)
    except (Exception, ExecInterrupt) as exc:
        return events, exc
    return events, None


def _types(events) -> list[str]:
    return [event.type for event in events]


_DROPPED = ProviderStreamError("RemoteProtocolError: peer closed connection", retryable=True)


@pytest.mark.parametrize(
    "first, before, restart",
    [
        (["stall"], ["start"], False),
        (["start", "stall"], ["start"], False),
        (["start", "thinking", "stall"], ["start", "thinking_start", "thinking_delta"], True),
        (["start", "text", "stall"], ["start", "text_start", "text_delta"], True),
        (["start", "tool", "stall"], ["start", "toolcall_start", "toolcall_end"], True),
        (["start", "text", _DROPPED], ["start", "text_start", "text_delta"], True),
        ([ConnectionResetError("reset by peer")], ["start"], False),
    ],
    ids=["stall-before-start", "stall-after-start", "stall-after-reasoning", "stall-after-text",
         "stall-after-tool-call", "drop-after-text", "drop-before-start"],
)
def test_a_reply_that_fails_in_transport_is_sent_again(first, before, restart):
    provider = Scripted(first, ["start", "text", "done"])
    state = RecoveryState()

    events, error = asyncio.run(_read(provider, state=state))

    assert error is None
    assert provider.calls == 2
    assert provider.closed == 2  # the failed attempt's stream was torn down
    # One reply to the consumer: a single start, the retry, then the new attempt.
    assert _types(events) == before + ["retry", "text_start", "text_delta", "done"]
    retry = events[len(before)]
    assert (retry.reason, retry.attempt, bool(retry.restart)) == ("transport", 2, restart)
    assert retry.max_attempts == stream_retry.PROVIDER_STREAM_MAX_ATTEMPTS
    assert [block.text for block in events[-1].message.content] == ["answer"]
    # The request got through: its re-send is not charged to the invocation.
    assert state.transport_used == 0 and state.transport_reserves == 1


def test_resends_stop_at_the_request_bound_and_say_so(monkeypatch):
    monkeypatch.setattr(stream_retry, "PROVIDER_STREAM_MAX_ATTEMPTS", 3)
    provider = Scripted(["start", "stall"])

    events, error = asyncio.run(_read(provider, state=RecoveryState()))

    assert isinstance(error, StreamIdleTimeout)
    assert provider.calls == 3
    assert [event.attempt for event in events if event.type == "retry"] == [2, 3]
    # No layer above spends another retry on it.
    assert error.transport_exhausted is True


def test_resends_share_the_invocations_transport_allowance():
    provider = Scripted(["start", "stall"])
    state = RecoveryState(transport_limit=1)

    _, error = asyncio.run(_read(provider, state=state))

    assert isinstance(error, StreamIdleTimeout) and error.transport_exhausted is True
    assert provider.calls == 2
    assert state.transport_used == 1


def test_recovery_turned_off_means_one_attempt():
    provider = Scripted(["start", "stall"], ["start", "done"])
    _, error = asyncio.run(_read(provider, state=RecoveryState(limit=0)))
    assert isinstance(error, StreamIdleTimeout)
    assert provider.calls == 1


@pytest.mark.parametrize(
    "failure",
    [
        ProviderStreamError("HTTP 429: slow down", http_status=429, retryable=True),
        ProviderStreamError("HTTP 400: bad request", http_status=400, retryable=False),
        ProviderStreamError("HTTP 503: overloaded", http_status=503, retryable=True),
        ValueError("the adapter could not build the request"),
    ],
    ids=["rate-limit", "bad-request", "overloaded", "local-error"],
)
def test_a_failure_that_is_not_transport_is_not_sent_again(failure):
    provider = Scripted(["start", failure], ["start", "done"])
    events, error = asyncio.run(_read(provider, state=RecoveryState()))
    assert error is failure
    assert provider.calls == 1
    assert _types(events) == ["start"]


def test_a_failure_the_adapter_already_retried_is_not_sent_again():
    spent = ProviderStreamError("ReadError: connection lost", retryable=True)
    spent.transport_exhausted = True
    provider = Scripted(["start", spent], ["start", "done"])
    _, error = asyncio.run(_read(provider, state=RecoveryState()))
    assert error is spent and provider.calls == 1


def test_an_adapter_that_bounds_its_own_idle_is_still_sent_again():
    # Bounding the idle wait itself (Gemini subscription) is not a retry loop.
    dropped = StreamIdleTimeout("no gemini-subscription event for 300s")
    provider = Scripted(["start", dropped], ["start", "text", "done"], bounds_own_stream_idle=True)
    events, error = asyncio.run(_read(provider, state=RecoveryState()))
    assert error is None and provider.calls == 2
    assert [event.restart for event in events if event.type == "retry"] == [None]


def test_a_governed_request_is_one_attempt(monkeypatch):
    class Reservation:
        def clamp(self, options, model=None):
            return options

        def start(self):
            pass

        def settle(self, *args, **kwargs):
            pass

        def release(self):
            pass

    monkeypatch.setattr(budget_module.BudgetedRequest, "begin", classmethod(lambda cls, *a, **k: Reservation()))
    provider = Scripted(["start", "stall"], ["start", "done"])

    _, error = asyncio.run(_read(provider, state=RecoveryState()))

    assert isinstance(error, StreamIdleTimeout)
    assert provider.calls == 1
    assert not getattr(error, "transport_exhausted", False)


def test_a_stop_during_the_wait_ends_the_request(monkeypatch):
    monkeypatch.setattr(stream_retry, "_BACKOFF_BASE_S", 30.0)
    provider = Scripted(["start", "stall"], ["start", "done"])
    signal = asyncio.Event()

    async def main():
        task = asyncio.ensure_future(_read(provider, SimpleStreamOptions(api_key="k", signal=signal),
                                           RecoveryState()))
        await asyncio.sleep(IDLE_S + 0.3)  # the stall has ended; the request is waiting to go again
        signal.set()
        return await asyncio.wait_for(task, timeout=2.0)

    events, error = asyncio.run(main())
    assert isinstance(error, ExecInterrupt)
    assert provider.calls == 1
    assert _types(events) == ["start", "retry"]


@pytest.fixture
def ledger_model():
    """A model id of this test's own, and the ledger's rows for it."""
    import uuid

    from openprogram.models.usage.ledger import default_ledger

    model_id = f"resend-{uuid.uuid4().hex[:8]}"

    def row():
        rows = default_ledger.query(group_by=["model_id"], filters={"model_id": model_id})
        return rows[0] if rows else None

    return model_id, row


def test_a_stalled_request_is_counted_with_its_cost_unknown(ledger_model):
    model_id, row = ledger_model
    provider = Scripted(["start", "text", "stall"], ["start", "text", "done"])
    asyncio.run(_read(provider, state=RecoveryState(), model_id=model_id))

    usage = row()
    assert usage.events == 2  # the stalled request and the one that answered
    assert usage.unknown_cost_events == 1
    assert (usage.input_tokens, usage.output_tokens) == (10, 2)


def test_a_failed_request_records_the_tokens_it_reported(ledger_model):
    model_id, row = ledger_model
    dropped = ProviderStreamError("ReadError: connection lost", retryable=True)
    provider = Scripted(["usage", "text", dropped])
    _, error = asyncio.run(_read(provider, state=RecoveryState(limit=0), model_id=model_id))
    assert error is dropped

    usage = row()
    assert usage.events == 1
    assert usage.input_tokens == 40
    assert usage.unknown_cost_events == 0  # priced from the tokens it reported


def test_a_request_refused_before_any_event_is_not_counted(ledger_model):
    model_id, row = ledger_model
    refused = ProviderStreamError("HTTP 401: bad key", http_status=401, retryable=False)
    provider = Scripted([refused])
    _, error = asyncio.run(_read(provider, state=RecoveryState(), model_id=model_id))
    assert error is refused
    assert row() is None


# ── The real adapters: ``start`` is emitted before anything is sent ────────


def _registered(api: str):
    """The built-in provider registered for ``api``. Registers the built-ins
    first: an earlier test in this worker may have left the registry without
    them, and ``register_builtins`` is what restores them."""
    from openprogram.models.providers.catalog.api_registry import get_api_provider
    from openprogram.models.providers.catalog.register import register_builtins

    register_builtins()
    provider = get_api_provider(api)
    assert provider is not None, f"no built-in provider for {api}"
    return provider


def _openai_responses(monkeypatch, failure: BaseException):
    """The registered OpenAI Responses provider with a client that fails on send."""
    from openprogram.models.providers.adapters.openai_responses import openai_responses

    sends = []

    class Responses:
        async def create(self, **params):
            sends.append(params)
            raise failure

    client = type("Client", (), {"responses": Responses()})()
    monkeypatch.setattr(openai_responses, "_create_client", lambda *args, **kwargs: client)
    return _registered("openai-responses"), sends


async def _read_real(provider, model: Model):
    current_recovery.set(RecoveryState())
    events = []
    try:
        async for event in stream_simple_with_provider(
            provider, model, _context(), SimpleStreamOptions(api_key="k"),
        ):
            events.append(event)
    except Exception as exc:  # noqa: BLE001 - the failure is the result under test
        return events, exc
    return events, None


def test_a_refusal_after_the_adapters_start_event_is_not_counted(monkeypatch, ledger_model):
    import httpx
    import openai

    model_id, row = ledger_model
    refused = openai.AuthenticationError(
        "bad key", body=None,
        response=httpx.Response(401, request=httpx.Request("POST", "http://resend.invalid")),
    )
    provider, sends = _openai_responses(monkeypatch, refused)
    model = _model(model_id).model_copy(update={"api": "openai-responses", "provider": "openai"})

    events, error = asyncio.run(_read_real(provider, model))

    assert error is refused
    assert len(sends) == 1
    assert _types(events) == ["start"]  # the adapter said start before it sent
    assert row() is None


def test_a_connection_never_made_is_sent_again_and_not_counted(monkeypatch, ledger_model):
    monkeypatch.setattr(stream_retry, "PROVIDER_STREAM_MAX_ATTEMPTS", 3)
    model_id, row = ledger_model
    provider, sends = _openai_responses(monkeypatch, ConnectionRefusedError("connection refused"))
    model = _model(model_id).model_copy(update={"api": "openai-responses", "provider": "openai"})

    events, error = asyncio.run(_read_real(provider, model))

    assert isinstance(error, ConnectionRefusedError)
    assert len(sends) == 3
    assert _types(events) == ["start", "retry", "retry"]
    assert row() is None


def test_a_dropped_google_connection_is_sent_again(monkeypatch):
    from google import genai
    from google.genai import types as gtypes

    answer = gtypes.GenerateContentResponse(candidates=[gtypes.Candidate(
        finish_reason=gtypes.FinishReason.STOP,
        content=gtypes.Content(parts=[gtypes.Part(text="answer")]),
    )])
    sends = []

    class Models:
        async def generate_content_stream(self, **kwargs):
            sends.append(kwargs)
            if len(sends) == 1:
                raise ConnectionResetError("connection reset by peer")

            async def chunks():
                yield answer
            return chunks()

    class Client:
        def __init__(self, **kwargs):
            self.aio = type("Aio", (), {"models": Models()})()

    monkeypatch.setattr(genai, "Client", Client)
    model = Model(id="gemini-resend", name="Gemini", api="google-generative-ai", provider="google",
                  base_url="https://generativelanguage.googleapis.com/v1beta")

    events, error = asyncio.run(_read_real(_registered("google-generative-ai"), model))

    assert error is None
    assert len(sends) == 2
    assert _types(events)[:2] == ["start", "retry"]
    assert events[-1].type == "done"
    assert "".join(block.text for block in events[-1].message.content) == "answer"


def test_a_stopped_google_stream_still_ends_as_aborted(monkeypatch):
    from google import genai

    from openprogram.models.providers.adapters.google import google

    stop = asyncio.Event()

    class Models:
        async def generate_content_stream(self, **kwargs):
            stop.set()
            raise ConnectionResetError("torn down by the stop")

    class Client:
        def __init__(self, **kwargs):
            self.aio = type("Aio", (), {"models": Models()})()

    monkeypatch.setattr(genai, "Client", Client)
    model = Model(id="gemini-resend", name="Gemini", api="google-generative-ai", provider="google",
                  base_url="https://generativelanguage.googleapis.com/v1beta")

    async def consume():
        return [event async for event in google.stream_simple(
            model, _context(), SimpleStreamOptions(api_key="k", signal=stop))]

    events = asyncio.run(consume())
    assert _types(events) == ["start", "error"]
    assert events[-1].reason == "aborted"
