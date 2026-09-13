"""Event-sequence unit tests for the provider stream fixes (no network).

Covers:
  * Responses stream assembler: interleaved parallel tool calls must each
    keep their own arguments (the single-cursor bug routed idx0 deltas
    onto idx1's block and both finalized as ``{}``).
  * codex retry: each attempt restarts from the committed prefix instead
    of appending to the shared ``output``.
  * Cancel signal: stream readers observe ``SimpleStreamOptions.signal``
    mid-stream (StreamAborted) and finalize the turn as ``aborted``.
  * gemini-cli error path uses ``ev_stream.fail`` so the consumer raises
    instead of seeing a clean stream end (auto-retry of a failed stream).
  * anthropic / openai_completions re-raise real exceptions instead of
    swallowing them into EventError.
"""
from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from openprogram.providers._shared.openai_responses import process_responses_stream
from openprogram.providers.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    Usage,
    UserMessage,
)
from openprogram.providers.utils.errors import StreamAborted


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

def _model(api: str = "openai-responses", provider: str = "openai") -> Model:
    return Model(
        id="test-model", name="test", api=api, provider=provider,
        base_url="https://example.invalid",
    )


def _output(model: Model) -> AssistantMessage:
    return AssistantMessage(
        content=[], api=model.api, provider=model.provider, model=model.id,
        usage=Usage(), stop_reason="stop", timestamp=0,
    )


class _Collector:
    """Minimal stand-in for EventStream.push at the assembler level."""

    def __init__(self):
        self.events = []

    def push(self, evt):
        self.events.append(evt)


async def _feed(events):
    for e in events:
        yield e


def _run_assembler(events, signal=None):
    model = _model()
    output = _output(model)
    collector = _Collector()
    asyncio.run(process_responses_stream(
        _feed(events), output, collector, model, signal=signal,
    ))
    return output, collector


def _fc_added(idx, call_id, name):
    return {
        "type": "response.output_item.added", "output_index": idx,
        "item": {"type": "function_call", "id": f"fc_{idx}",
                 "call_id": call_id, "name": name, "arguments": ""},
    }


def _fc_delta(idx, delta):
    return {"type": "response.function_call_arguments.delta",
            "output_index": idx, "delta": delta}


def _fc_done(idx, call_id, name, args):
    return {
        "type": "response.output_item.done", "output_index": idx,
        "item": {"type": "function_call", "id": f"fc_{idx}",
                 "call_id": call_id, "name": name, "arguments": args},
    }


_COMPLETED = {
    "type": "response.completed",
    "response": {"status": "completed",
                 "usage": {"input_tokens": 1, "output_tokens": 1,
                           "total_tokens": 2}},
}


def _tool_calls(output):
    return [b for b in output.content
            if getattr(b, "type", None) == "toolCall"
            or (isinstance(b, dict) and b.get("type") == "toolCall")]


def _args(block):
    return block["arguments"] if isinstance(block, dict) else block.arguments


# ---------------------------------------------------------------------------
# 1. Interleaved multi-tool-call argument assembly
# ---------------------------------------------------------------------------

def test_interleaved_tool_call_arguments_do_not_cross():
    output, collector = _run_assembler([
        _fc_added(0, "call_0", "get_weather"),
        _fc_added(1, "call_1", "get_time"),
        _fc_delta(0, '{"city": '),
        _fc_delta(1, '{"tz": "UTC"}'),
        _fc_delta(0, '"Paris"}'),
        {"type": "response.function_call_arguments.done", "output_index": 0,
         "arguments": '{"city": "Paris"}'},
        {"type": "response.function_call_arguments.done", "output_index": 1,
         "arguments": '{"tz": "UTC"}'},
        _fc_done(0, "call_0", "get_weather", '{"city": "Paris"}'),
        _fc_done(1, "call_1", "get_time", '{"tz": "UTC"}'),
        _COMPLETED,
    ])

    calls = _tool_calls(output)
    assert len(calls) == 2
    assert _args(calls[0]) == {"city": "Paris"}
    assert _args(calls[1]) == {"tz": "UTC"}
    assert output.stop_reason == "toolUse"

    # The toolcall_end events must carry the right args at the right index.
    ends = [e for e in collector.events if e["type"] == "toolcall_end"]
    assert [e["tool_call"]["arguments"] for e in ends] == [
        {"city": "Paris"}, {"tz": "UTC"},
    ]
    assert [e["content_index"] for e in ends] == [0, 1]


def test_sequential_tool_call_arguments_still_correct():
    output, _ = _run_assembler([
        _fc_added(0, "call_0", "get_weather"),
        _fc_delta(0, '{"city": "Paris"}'),
        _fc_done(0, "call_0", "get_weather", '{"city": "Paris"}'),
        _fc_added(1, "call_1", "get_time"),
        _fc_delta(1, '{"tz": "UTC"}'),
        _fc_done(1, "call_1", "get_time", '{"tz": "UTC"}'),
        _COMPLETED,
    ])
    calls = _tool_calls(output)
    assert len(calls) == 2
    assert _args(calls[0]) == {"city": "Paris"}
    assert _args(calls[1]) == {"tz": "UTC"}


def test_events_without_output_index_fall_back_to_open_block():
    # Older event shapes carry no output_index — sequential streams must
    # still assemble (fallback: most recent open block of the right type).
    output, _ = _run_assembler([
        {"type": "response.output_item.added",
         "item": {"type": "function_call", "id": "fc", "call_id": "c",
                  "name": "f", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "delta": '{"a": 1}'},
        {"type": "response.output_item.done",
         "item": {"type": "function_call", "id": "fc", "call_id": "c",
                  "name": "f", "arguments": '{"a": 1}'}},
        _COMPLETED,
    ])
    calls = _tool_calls(output)
    assert len(calls) == 1
    assert _args(calls[0]) == {"a": 1}


def test_interleaved_text_and_tool_blocks_route_by_index():
    output, _ = _run_assembler([
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "message", "id": "msg_0"}},
        _fc_added(1, "call_1", "get_time"),
        {"type": "response.output_text.delta", "output_index": 0, "delta": "Hel"},
        _fc_delta(1, '{"tz": "UTC"}'),
        {"type": "response.output_text.delta", "output_index": 0, "delta": "lo"},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "message", "id": "msg_0",
                  "content": [{"text": "Hello"}]}},
        _fc_done(1, "call_1", "get_time", '{"tz": "UTC"}'),
        _COMPLETED,
    ])
    text_blocks = [b for b in output.content
                   if getattr(b, "type", None) == "text"
                   or (isinstance(b, dict) and b.get("type") == "text")]
    assert len(text_blocks) == 1
    text = text_blocks[0]["text"] if isinstance(text_blocks[0], dict) else text_blocks[0].text
    assert text == "Hello"
    assert _args(_tool_calls(output)[0]) == {"tz": "UTC"}


# ---------------------------------------------------------------------------
# Fake codex HTTP transport
# ---------------------------------------------------------------------------

class _FakeSSEResponse:
    status_code = 200
    headers: dict = {}

    def __init__(self, lines, fail_after=None, set_signal_after=None):
        self._lines = list(lines)
        self._fail_after = fail_after       # exception raised after all lines
        self._set_signal_after = set_signal_after  # (index, event) → set after line i

    async def aiter_lines(self):
        for i, line in enumerate(self._lines):
            yield line
            if self._set_signal_after and i == self._set_signal_after[0]:
                self._set_signal_after[1].set()
        if self._fail_after is not None:
            raise self._fail_after

    async def aread(self):
        return b""


class _FakeStreamCM:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeHTTPClient:
    """Serves one queued response per .stream() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.contents = []

    def stream(self, method, url, headers=None, content=None):
        self.calls += 1
        self.contents.append(content)
        return _FakeStreamCM(self._responses.pop(0))

    async def aclose(self):
        return None


def _sse(obj):
    return "data: " + json.dumps(obj)


_CODEX_HEADERS = {"originator": "codex_cli_rs", "version": "1"}


def _codex_ctx():
    return Context(system_prompt="sys", messages=[UserMessage(content="hi", timestamp=0)])


# ---------------------------------------------------------------------------
# 2. codex retry restarts from the committed prefix
# ---------------------------------------------------------------------------

def test_codex_retry_does_not_duplicate_blocks(monkeypatch):
    mod = importlib.import_module(
        "openprogram.providers.openai_codex.openai_codex")

    attempt1 = _FakeSSEResponse([
        _sse({"type": "response.output_item.added", "output_index": 0,
              "item": {"type": "message", "id": "m0"}}),
        _sse({"type": "response.output_text.delta", "output_index": 0,
              "delta": "Hello"}),
    ], fail_after=ConnectionError("mid-stream drop"))
    attempt2 = _FakeSSEResponse([
        _sse({"type": "response.output_item.added", "output_index": 0,
              "item": {"type": "message", "id": "m0"}}),
        _sse({"type": "response.output_text.delta", "output_index": 0,
              "delta": "Hello world"}),
        _sse({"type": "response.output_item.done", "output_index": 0,
              "item": {"type": "message", "id": "m0",
                       "content": [{"text": "Hello world"}]}}),
        _sse(_COMPLETED),
        "data: [DONE]",
    ])
    fake_client = _FakeHTTPClient([attempt1, attempt2])
    monkeypatch.setattr(
        mod, "get_shared_async_client", lambda _name, **_kwargs: fake_client
    )
    monkeypatch.setattr(mod, "build_async_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(mod, "_resolve_codex_bearer_token", lambda k: "tok")

    # Force one retry regardless of the committed check — the point under
    # test is the attempt-restart contract, not the retry gating.
    async def fake_retry_stream(attempt_fn, *, is_committed_fn, **kw):
        try:
            await attempt_fn()
        except Exception:
            await attempt_fn()

    monkeypatch.setattr(mod, "retry_stream", fake_retry_stream)

    async def run():
        stream = mod.stream_openai_codex_responses(
            _model(api="openai-codex", provider="openai-codex"), _codex_ctx(),
            {"api_key": "tok", "headers": dict(_CODEX_HEADERS)},
        )
        return await stream.result()

    msg = asyncio.run(run())
    assert fake_client.calls == 2
    texts = [b for b in msg.content
             if getattr(b, "type", None) == "text"
             or (isinstance(b, dict) and b.get("type") == "text")]
    assert len(texts) == 1  # no leftover block from the failed attempt
    text = texts[0]["text"] if isinstance(texts[0], dict) else texts[0].text
    assert text == "Hello world"


def test_codex_retries_done_after_empty_reasoning_placeholder(monkeypatch):
    mod = importlib.import_module(
        "openprogram.providers.openai_codex.openai_codex")

    premature = _FakeSSEResponse([
        _sse({"type": "response.output_item.added", "output_index": 0,
              "item": {"type": "reasoning", "id": "r0", "summary": []}}),
        "data: [DONE]",
    ])
    still_premature = _FakeSSEResponse([
        _sse({"type": "response.output_item.added", "output_index": 0,
              "item": {"type": "reasoning", "id": "r1", "summary": []}}),
        "data: [DONE]",
    ])
    recovered = _FakeSSEResponse([
        _sse({"type": "response.output_item.added", "output_index": 0,
              "item": {"type": "message", "id": "m0"}}),
        _sse({"type": "response.output_text.delta", "output_index": 0,
              "delta": "recovered"}),
        _sse({"type": "response.output_item.done", "output_index": 0,
              "item": {"type": "message", "id": "m0",
                       "content": [{"text": "recovered"}]}}),
        _sse(_COMPLETED),
        "data: [DONE]",
    ])
    fake_client = _FakeHTTPClient([premature, still_premature, recovered])
    private_client_builds = []
    monkeypatch.setattr(
        mod, "get_shared_async_client", lambda _name, **_kwargs: fake_client
    )

    def build_private(**_kwargs):
        private_client_builds.append(True)
        return fake_client

    monkeypatch.setattr(mod, "build_async_client", build_private)
    monkeypatch.setattr(mod, "_resolve_codex_bearer_token", lambda _k: "tok")

    async def retry_once(attempt_fn, *, is_committed_fn, **_kwargs):
        with pytest.raises(mod.ProviderStreamError, match="before a terminal"):
            await attempt_fn()
        assert is_committed_fn() is False
        with pytest.raises(mod.ProviderStreamError, match="before a terminal"):
            await attempt_fn()
        assert is_committed_fn() is False
        await attempt_fn()

    monkeypatch.setattr(mod, "retry_stream", retry_once)

    async def run():
        stream = mod.stream_openai_codex_responses(
            Model(
                id="test-model", name="test", api="openai-codex",
                provider="openai-codex", base_url="https://example.invalid",
                reasoning=True,
            ),
            _codex_ctx(),
            {"api_key": "tok", "headers": dict(_CODEX_HEADERS),
             "reasoning_effort": "medium", "session_id": "op-original"},
        )
        return await stream.result()

    message = asyncio.run(run())
    assert fake_client.calls == 3
    assert len(private_client_builds) == 2
    assert [block.text for block in message.content if block.type == "text"] == [
        "recovered"
    ]
    attempts = [json.loads(content) for content in fake_client.contents]
    assert [attempts[0]["reasoning"]["effort"],
            attempts[1]["reasoning"]["effort"]] == [
        "medium",
        "low",
    ]
    assert "reasoning" not in attempts[2]
    assert "include" not in attempts[2]
    assert attempts[0]["prompt_cache_key"] == "op-original"
    assert attempts[1]["prompt_cache_key"].startswith("op-retry-")
    assert attempts[2]["prompt_cache_key"].startswith("op-retry-")
    assert len({attempt["prompt_cache_key"] for attempt in attempts}) == 3


def test_codex_preserves_partial_text_when_terminal_event_is_missing(monkeypatch):
    mod = importlib.import_module(
        "openprogram.providers.openai_codex.openai_codex")

    partial = _FakeSSEResponse([
        _sse({"type": "response.output_item.added", "output_index": 0,
              "item": {"type": "message", "id": "m0"}}),
        _sse({"type": "response.output_text.delta", "output_index": 0,
              "delta": "first line\n"}),
        _sse({"type": "response.output_text.delta", "output_index": 0,
              "delta": "second line"}),
        "data: [DONE]",
    ])
    fake_client = _FakeHTTPClient([partial])
    monkeypatch.setattr(
        mod, "get_shared_async_client", lambda _name, **_kwargs: fake_client
    )
    monkeypatch.setattr(mod, "build_async_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(mod, "_resolve_codex_bearer_token", lambda _k: "tok")

    async def run():
        stream = mod.stream_openai_codex_responses(
            _model(api="openai-codex", provider="openai-codex"),
            _codex_ctx(),
            {"api_key": "tok", "headers": dict(_CODEX_HEADERS)},
        )
        return await stream.result()

    message = asyncio.run(run())
    assert fake_client.calls == 1
    assert message.stop_reason == "length"
    texts = [
        block.get("text") if isinstance(block, dict) else block.text
        for block in message.content
        if (block.get("type") if isinstance(block, dict) else block.type) == "text"
    ]
    assert texts == ["first line\nsecond line"]


# ---------------------------------------------------------------------------
# 3. Cancel signal
# ---------------------------------------------------------------------------

def test_parse_sse_stream_raises_stream_aborted_when_signal_set():
    mod = importlib.import_module(
        "openprogram.providers.openai_codex.openai_codex")

    async def run():
        sig = asyncio.Event()
        sig.set()
        agen = mod._parse_sse_stream(
            _FakeSSEResponse([_sse({"type": "x"})]), signal=sig)
        with pytest.raises(StreamAborted):
            await agen.__anext__()

    asyncio.run(run())


def test_parse_sse_stream_cancel_poll_does_not_cancel_network_read():
    mod = importlib.import_module(
        "openprogram.providers.openai_codex.openai_codex")

    class DelayedResponse:
        async def aiter_lines(self):
            # Longer than the 250 ms Stop-button poll interval. The old
            # wait_for(__anext__) implementation cancelled this healthy read.
            await asyncio.sleep(0.35)
            yield _sse({"type": "response.created", "response": {}})

    async def run():
        signal = asyncio.Event()
        stream = mod._parse_sse_stream(DelayedResponse(), signal=signal)
        assert await stream.__anext__() == {
            "type": "response.created",
            "response": {},
        }
        await stream.aclose()

    asyncio.run(run())


def test_parse_sse_stream_cancels_pending_read_when_signal_arrives():
    mod = importlib.import_module(
        "openprogram.providers.openai_codex.openai_codex")

    class SlowResponse:
        async def aiter_lines(self):
            await asyncio.sleep(60)
            yield _sse({"type": "never"})

    async def run():
        signal = asyncio.Event()
        stream = mod._parse_sse_stream(SlowResponse(), signal=signal)
        pending = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0.05)
        signal.set()
        with pytest.raises(StreamAborted):
            await asyncio.wait_for(pending, timeout=1.0)

    asyncio.run(run())


def test_process_responses_stream_raises_stream_aborted_mid_stream():
    sig = asyncio.Event()

    async def events():
        yield {"type": "response.output_item.added", "output_index": 0,
               "item": {"type": "message", "id": "m0"}}
        sig.set()
        yield {"type": "response.output_text.delta", "output_index": 0,
               "delta": "never processed"}

    model = _model()
    output = _output(model)

    async def run():
        with pytest.raises(StreamAborted):
            await process_responses_stream(
                events(), output, _Collector(), model, signal=sig)

    asyncio.run(run())
    # The first block exists; the post-cancel delta never landed.
    assert output.content[0]["text"] == ""


def test_codex_signal_finalizes_stream_as_aborted(monkeypatch):
    mod = importlib.import_module(
        "openprogram.providers.openai_codex.openai_codex")
    sig = asyncio.Event()

    resp = _FakeSSEResponse([
        _sse({"type": "response.output_item.added", "output_index": 0,
              "item": {"type": "message", "id": "m0"}}),
        _sse({"type": "response.output_text.delta", "output_index": 0,
              "delta": "partial"}),
        _sse({"type": "response.output_text.delta", "output_index": 0,
              "delta": " never-read"}),
    ], set_signal_after=(1, sig))
    fake_client = _FakeHTTPClient([resp])
    monkeypatch.setattr(
        mod, "get_shared_async_client", lambda _name, **_kwargs: fake_client
    )
    monkeypatch.setattr(mod, "_resolve_codex_bearer_token", lambda k: "tok")

    async def run():
        stream = mod.stream_openai_codex_responses(
            _model(api="openai-codex", provider="openai-codex"), _codex_ctx(),
            {"api_key": "tok", "headers": dict(_CODEX_HEADERS),
             "signal": sig},
        )
        events = []
        async for ev in stream:  # must NOT raise: cancel is not an error
            events.append(ev)
        return events, await stream.result()

    events, result = asyncio.run(run())
    assert result.stop_reason == "aborted"
    last = events[-1]
    assert last.type == "error"
    assert last.reason == "aborted"


def test_gemini_cli_signal_finalizes_stream_as_aborted(monkeypatch):
    gmod = importlib.import_module(
        "openprogram.providers.google_gemini_cli.google_gemini_cli")
    sig = asyncio.Event()

    resp = _FakeSSEResponse([
        'data: {"candidates": [{"content": {"parts": [{"text": "hel"}]}}]}',
        'data: {"candidates": [{"content": {"parts": [{"text": "lo"}]}}]}',
    ], set_signal_after=(0, sig))
    monkeypatch.setattr(
        gmod, "build_async_client", lambda **_kwargs: _FakeGeminiClient(resp))

    async def run():
        stream = gmod.stream_google_gemini_cli(
            _model(api="google-gemini-cli", provider="google-gemini-cli"),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            {"api_key": "k", "project_id": "p", "signal": sig},
        )
        events = []
        async for ev in stream:
            events.append(ev)
        return events, await stream.result()

    events, result = asyncio.run(run())
    assert result["stop_reason"] == "aborted"
    assert events[-1].type == "error"
    assert events[-1].reason == "aborted"


# ---------------------------------------------------------------------------
# 5. gemini-cli error path fails the stream
# ---------------------------------------------------------------------------

class _FakeGeminiClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, content=None):
        return _FakeStreamCM(self._resp)


class _Fake400Response:
    status_code = 400
    headers: dict = {}

    async def aread(self):
        return b'{"error": "bad request"}'


class _Fake404Response(_Fake400Response):
    status_code = 404


def test_gemini_cli_error_fails_stream_instead_of_clean_end(monkeypatch):
    gmod = importlib.import_module(
        "openprogram.providers.google_gemini_cli.google_gemini_cli")
    monkeypatch.setattr(
        gmod, "build_async_client",
        lambda **_kwargs: _FakeGeminiClient(_Fake400Response()))

    async def run():
        stream = gmod.stream_google_gemini_cli(
            _model(api="google-gemini-cli", provider="google-gemini-cli"),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            {"api_key": "k", "project_id": "p"},
        )
        with pytest.raises(RuntimeError, match="HTTP 400"):
            async for _ in stream:
                pass

    asyncio.run(run())


@pytest.mark.parametrize(("governed", "expected"), [(False, 4), (True, 1)])
def test_bedrock_config_uses_total_attempt_semantics(monkeypatch, governed, expected):
    monkeypatch.setattr(
        "openprogram.agent.job.runner.current_job_resource_context",
        lambda: ("task", object()) if governed else None,
    )
    monkeypatch.delenv("OPENPROGRAM_BEDROCK_MAX_RETRIES", raising=False)
    mod = importlib.import_module(
        "openprogram.providers.amazon_bedrock.amazon_bedrock",
    )

    config = mod._build_boto_retry_config()

    assert config.retries["total_max_attempts"] == expected
    assert "max_attempts" not in config.retries


@pytest.mark.parametrize(("governed", "expected_calls"), [(False, 2), (True, 1)])
def test_gemini_cli_endpoint_fallback_obeys_attempt_boundary(
    monkeypatch, governed, expected_calls,
):
    mod = importlib.import_module(
        "openprogram.providers.google_gemini_cli.google_gemini_cli",
    )
    monkeypatch.setattr(
        "openprogram.agent.job.runner.current_job_resource_context",
        lambda: ("task", object()) if governed else None,
    )
    monkeypatch.setattr(
        mod, "_resolve_endpoints", lambda _model: ["https://one", "https://two"],
    )
    calls = []

    class Client(_FakeGeminiClient):
        def stream(self, method, url, headers=None, content=None):
            calls.append(url)
            return super().stream(method, url, headers=headers, content=content)

    monkeypatch.setattr(
        mod, "build_async_client", lambda **_kwargs: Client(_Fake404Response()),
    )

    async def run():
        stream = mod.stream_google_gemini_cli(
            _model(api="google-gemini-cli", provider="google-gemini-cli"),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            {"api_key": "k", "project_id": "p"},
        )
        with pytest.raises(RuntimeError, match="HTTP 404"):
            async for _ in stream:
                pass

    asyncio.run(run())
    assert len(calls) == expected_calls


# ---------------------------------------------------------------------------
# 6. anthropic / openai_completions re-raise instead of swallowing
# ---------------------------------------------------------------------------

class _FakeAntStream:
    def __init__(self, exc=None):
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._exc is not None:
            raise self._exc
        raise StopAsyncIteration

    async def get_final_message(self):
        raise RuntimeError("no final message")


class _FakeAntStreamCM:
    def __init__(self, exc=None):
        self._exc = exc

    async def __aenter__(self):
        return _FakeAntStream(self._exc)

    async def __aexit__(self, *exc):
        return False


class _FakeAnthropicClient:
    def __init__(self, exc=None):
        self.messages = SimpleNamespace(
            stream=lambda **params: _FakeAntStreamCM(exc))


def _anthropic_model():
    return _model(api="anthropic-messages", provider="anthropic")


def test_anthropic_reraises_stream_exception(monkeypatch):
    amod = importlib.import_module("openprogram.providers.anthropic.anthropic")
    from openprogram.auth import usage as auth_usage
    monkeypatch.setattr(auth_usage, "acquire_pooled", lambda p: None)
    monkeypatch.setattr(
        amod, "_build_client",
        lambda *a, **k: (_FakeAnthropicClient(RuntimeError("boom")), False))

    async def run():
        gen = amod.stream_simple(
            _anthropic_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            SimpleStreamOptions(api_key="sk-test"),
        )
        first = await gen.__anext__()
        assert first.type == "start"
        with pytest.raises(RuntimeError, match="boom"):
            async for _ in gen:
                pass

    asyncio.run(run())


def test_anthropic_signal_yields_aborted_event(monkeypatch):
    amod = importlib.import_module("openprogram.providers.anthropic.anthropic")
    from openprogram.auth import usage as auth_usage
    monkeypatch.setattr(auth_usage, "acquire_pooled", lambda p: None)
    monkeypatch.setattr(
        amod, "_build_client",
        lambda *a, **k: (_FakeAnthropicClient(), False))

    sig = asyncio.Event()
    sig.set()

    async def run():
        gen = amod.stream_simple(
            _anthropic_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            SimpleStreamOptions(api_key="sk-test", signal=sig),
        )
        events = []
        async for ev in gen:
            events.append(ev)
        return events

    events = asyncio.run(run())
    assert events[-1].type == "error"
    assert events[-1].reason == "aborted"
    assert events[-1].error.stop_reason == "aborted"


class _FakeCompletionsStream:
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._exc


def test_openai_completions_reraises_stream_exception(monkeypatch):
    cmod = importlib.import_module(
        "openprogram.providers.openai_completions.openai_completions")
    from openprogram.auth import usage as auth_usage
    monkeypatch.setattr(auth_usage, "acquire_pooled", lambda p: None)

    class _FakeAsyncOpenAI:
        def __init__(self, **kw):
            async def _create(**params):
                return _FakeCompletionsStream(RuntimeError("kaboom"))
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=_create))

    monkeypatch.setattr(cmod._openai, "AsyncOpenAI", _FakeAsyncOpenAI)

    async def run():
        gen = cmod.stream_simple(
            _model(api="openai-completions", provider="openai"),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            SimpleStreamOptions(api_key="sk-test"),
        )
        first = await gen.__anext__()
        assert first.type == "start"
        with pytest.raises(RuntimeError, match="kaboom"):
            async for _ in gen:
                pass

    asyncio.run(run())


@pytest.mark.parametrize(("governed", "expected"), [(False, 3), (True, 0)])
def test_openai_sdk_retry_boundary_tracks_governed_request(
    monkeypatch, governed, expected,
):
    cmod = importlib.import_module(
        "openprogram.providers.openai_completions.openai_completions")
    from openprogram.auth import usage as auth_usage
    monkeypatch.setattr(auth_usage, "acquire_pooled", lambda _p: None)
    monkeypatch.setattr(
        "openprogram.agent.job.runner.current_job_resource_context",
        lambda: ("task", object()) if governed else None,
    )
    captured = {}

    class _FakeAsyncOpenAI:
        def __init__(self, **kw):
            captured.update(kw)
            async def _create(**params):
                return _FakeCompletionsStream(RuntimeError("stop"))
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

    monkeypatch.setattr(cmod._openai, "AsyncOpenAI", _FakeAsyncOpenAI)

    async def run():
        gen = cmod.stream_simple(
            _model(api="openai-completions", provider="openai"),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            SimpleStreamOptions(api_key="sk-test"),
        )
        await gen.__anext__()

    asyncio.run(run())
    assert captured["max_retries"] == expected


@pytest.mark.parametrize(("governed", "expected"), [(False, 3), (True, 0)])
def test_azure_sdk_retry_boundary_tracks_governed_request(
    monkeypatch, governed, expected,
):
    mod = importlib.import_module(
        "openprogram.providers.azure_openai_responses.azure_openai_responses")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://azure.example")
    monkeypatch.setattr(
        "openprogram.agent.job.runner.current_job_resource_context",
        lambda: ("task", object()) if governed else None,
    )
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import openai
    monkeypatch.setattr(openai, "AsyncAzureOpenAI", Client)
    mod._create_client(
        _model(api="azure-openai-responses", provider="azure-openai-responses"),
        "key", {},
    )
    assert captured["max_retries"] == expected


@pytest.mark.parametrize(("governed", "expected"), [(False, 4), (True, 1)])
def test_bedrock_config_uses_total_attempt_semantics(
    monkeypatch, governed, expected,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner.current_job_resource_context",
        lambda: ("task", object()) if governed else None,
    )
    monkeypatch.delenv("OPENPROGRAM_BEDROCK_MAX_RETRIES", raising=False)
    mod = importlib.import_module(
        "openprogram.providers.amazon_bedrock.amazon_bedrock")

    config = mod._build_boto_retry_config()

    assert config.retries["total_max_attempts"] == expected
    assert "max_attempts" not in config.retries


@pytest.mark.parametrize(("governed", "expected_calls"), [(False, 2), (True, 1)])
def test_gemini_cli_endpoint_fallback_obeys_attempt_boundary(
    monkeypatch, governed, expected_calls,
):
    mod = importlib.import_module(
        "openprogram.providers.google_gemini_cli.google_gemini_cli")
    monkeypatch.setattr(
        "openprogram.agent.job.runner.current_job_resource_context",
        lambda: ("task", object()) if governed else None,
    )
    monkeypatch.setattr(
        mod, "_resolve_endpoints", lambda _model: ["https://one", "https://two"],
    )
    calls = []

    class Client(_FakeGeminiClient):
        def stream(self, method, url, headers=None, content=None):
            calls.append(url)
            return super().stream(method, url, headers=headers, content=content)

    monkeypatch.setattr(
        mod, "build_async_client", lambda **_kwargs: Client(_Fake404Response()),
    )

    async def run():
        stream = mod.stream_google_gemini_cli(
            _model(api="google-gemini-cli", provider="google-gemini-cli"),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            {"api_key": "k", "project_id": "p"},
        )
        with pytest.raises(RuntimeError, match="HTTP 404"):
            async for _ in stream:
                pass

    asyncio.run(run())

    assert len(calls) == expected_calls
