"""Recording and offline replay of provider calls.

The recorder wraps the scripted provider (standing in for a real network
provider) at the API-registry chokepoint; the replayer serves the resulting
recording file back with no provider underneath it at all.
"""

from __future__ import annotations

import asyncio
import builtins
import importlib
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest


_POSIX_PERMISSION_AND_SYMLINK = pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX mode and symlink contract; Windows preserves inherited ACLs",
)

from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.api_registry import get_api_provider, register_api_provider
from openprogram.providers.recording import (
    PLACEHOLDER,
    RECORDING_FORMAT_VERSION,
    RecordingProvider,
    remove_secret_values,
)
from openprogram.providers.replay import (
    ReplayMismatch,
    ReplayProvider,
    RecordingFileError,
    read_recording_file,
)
from openprogram.providers.structured_output import StructuredOutputCapabilities
from openprogram.providers.utils.errors import LLMError
from openprogram.providers.types import (
    AssistantMessageEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall


_API = "record-replay-test-api"
_SECRET = "sk-livekey0123456789abcdef"
_STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _model() -> Model:
    return Model(
        id="record-replay-model",
        name="Record replay model",
        api=_API,
        provider="record-replay-provider",
        base_url="http://record-replay.invalid",
        headers={"Authorization": f"Bearer {_SECRET}", "X-Trace": "keep-me"},
    )


def _options() -> SimpleStreamOptions:
    return SimpleStreamOptions(
        api_key=_SECRET,
        headers={"Cookie": "session=abc123", "X-Client": "openprogram"},
    )


@pytest.fixture
def restore_api_registry() -> Iterator[None]:
    previous = get_api_provider(_API)
    yield
    from openprogram.providers import api_registry

    if previous is None:
        api_registry._registry.pop(_API, None)
        api_registry._original_registry.pop(_API, None)
    else:
        register_api_provider(_API, previous)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything under test opens a socket."""
    import socket

    real_connect = socket.socket.connect

    def refuse(sock, address, *args, **kwargs):
        # The Windows Proactor event loop implements its wakeup socketpair
        # with an internal loopback TCP connection. Allow only that runtime
        # plumbing; provider network access remains forbidden.
        if (
            os.name == "nt"
            and isinstance(address, tuple)
            and address
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return real_connect(sock, address, *args, **kwargs)
        raise AssertionError("replay must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _scripted_with(*responses) -> object:
    from tests.component.providers.scripted_provider import ScriptedProvider

    provider = ScriptedProvider()
    for steps in responses:
        provider.add_response(*steps)
    return provider


class _InterruptingProvider:
    """Provider source whose closure is observable by the public recorder wrapper."""

    requires_credentials = False

    def __init__(self, *, fail_after_first: bool = False) -> None:
        self.closed = False
        self.fail_after_first = fail_after_first

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        source = _scripted_with((ScriptedText("partial"),)).stream_simple(
            model, context, options
        )
        try:
            async for event in source:
                yield event
                if self.fail_after_first:
                    raise RuntimeError("provider disconnected")
                await asyncio.Event().wait()
        finally:
            self.closed = True
            await source.aclose()


class _SyncStartFailureProvider:
    requires_credentials = False

    def stream(self, model, context, options=None):
        raise RuntimeError("stream start failed")

    def stream_simple(self, model, context, options=None):
        raise RuntimeError("stream_simple start failed")


class _DuplicateTerminalProvider:
    requires_credentials = False

    def __init__(self) -> None:
        self.closed = False

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        source = _scripted_with((ScriptedText("done"),)).stream_simple(
            model, context, options
        )
        try:
            terminal = None
            async for event in source:
                terminal = event
                yield event
            assert terminal is not None
            yield terminal
        finally:
            self.closed = True
            await source.aclose()


class _TerminalCloseFailureProvider:
    requires_credentials = False

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        source = _scripted_with((ScriptedText("done"),)).stream_simple(
            model, context, options
        )
        try:
            async for event in source:
                yield event
        finally:
            await source.aclose()
            raise RuntimeError("source close failed")


def _drain_stream(model: Model, options: SimpleStreamOptions | None = None) -> list:
    from openprogram.providers import stream_simple

    async def run():
        return [
            event
            async for event in stream_simple(
                model,
                Context(messages=[UserMessage(content="hello", timestamp=0)]),
                options if options is not None else _options(),
            )
        ]

    return asyncio.run(run())


def test_recorded_recording_file_holds_no_secret_in_plain_text(
    tmp_path: Path, restore_api_registry
) -> None:
    """Dropping redaction would leak the key and the Authorization header."""
    recording_file = tmp_path / "recording.jsonl"
    register_api_provider(
        _API, RecordingProvider(_scripted_with((ScriptedText("done"),)), recording_file)
    )

    _drain_stream(_model())

    text = recording_file.read_text(encoding="utf-8")
    assert _SECRET not in text
    assert "session=abc123" not in text
    assert PLACEHOLDER in text
    assert "keep-me" in text  # non-secret headers survive

    lines = [json.loads(raw) for raw in text.splitlines()]
    assert RECORDING_FORMAT_VERSION == 2
    assert lines[0] == {"type": "header", "format_version": 2}
    request = next(line for line in lines if line["type"] == "request")
    assert request["options"]["api_key"] == PLACEHOLDER
    assert request["options"]["headers"]["Cookie"] == PLACEHOLDER
    assert request["model"]["headers"]["Authorization"] == PLACEHOLDER
    assert [line["type"] for line in lines[-2:]] == ["event", "call_end"]


def test_recording_omits_runtime_only_options_but_provider_receives_callback(
    tmp_path: Path, restore_api_registry
) -> None:
    recording_file = tmp_path / "runtime-options.jsonl"
    scripted = _scripted_with((ScriptedText("done"),))
    register_api_provider(_API, RecordingProvider(scripted, recording_file))

    callback = lambda payload, model: payload
    _drain_stream(_model(), SimpleStreamOptions(on_payload=callback))

    assert scripted.calls[0].options.on_payload is callback
    request = next(
        row
        for row in map(
            json.loads, recording_file.read_text(encoding="utf-8").splitlines()
        )
        if row["type"] == "request"
    )
    assert "signal" not in request["options"]
    assert "on_payload" not in request["options"]


def _runtime_for_registered_model(
    monkeypatch: pytest.MonkeyPatch, *, max_retries: int = 1
) -> Runtime:
    monkeypatch.setattr(
        "openprogram.providers.get_model", lambda provider, model_id: _model()
    )
    runtime = Runtime(
        model="record-replay-provider:record-replay-model", max_retries=max_retries
    )
    runtime.session_id = "record-replay-structured-session"
    return runtime


def test_structured_retry_records_v2_calls_and_replays_same_typed_result(
    tmp_path: Path,
    restore_api_registry,
    monkeypatch: pytest.MonkeyPatch,
    offline,
) -> None:
    recording_file = tmp_path / "structured-v2.jsonl"
    scripted = _scripted_with(
        (ScriptedText('{"answer":"bad"}'),),
        (ScriptedText('{"answer":2}'),),
    )
    recorder = RecordingProvider(scripted, recording_file)
    capabilities = StructuredOutputCapabilities(
        native="supported",
        dialect="test",
        streaming=True,
        with_tools=False,
        schema_profile="none",
    )
    register_api_provider(_API, recorder, capabilities)
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", "off")

    live = _runtime_for_registered_model(monkeypatch, max_retries=2).exec(
        "answer",
        response_format=_STRUCTURED_SCHEMA,
        toolset="none",
    )

    replay = ReplayProvider(recording_file)
    register_api_provider(_API, replay, capabilities)
    stream_module = importlib.import_module("openprogram.providers.stream")
    monkeypatch.setattr(
        stream_module,
        "resolve_provider_key",
        lambda provider: pytest.fail("structured replay resolved credentials"),
    )
    replayed = _runtime_for_registered_model(monkeypatch, max_retries=2).exec(
        "answer",
        response_format=_STRUCTURED_SCHEMA,
        toolset="none",
    )

    assert live == replayed == {"answer": 2}
    assert scripted.call_count == replay.call_count == 2
    replay.assert_consumed()
    rows = [
        json.loads(raw)
        for raw in recording_file.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0] == {"type": "header", "format_version": 2}
    requests = [row for row in rows if row["type"] == "request"]
    assert [row["call_index"] for row in requests] == [0, 1]
    assert requests[0]["options"]["response_format"] == {
        "schema": _STRUCTURED_SCHEMA,
        "name": "response",
        "description": None,
        "strict": True,
        "fallback": "auto",
        "max_validation_retries": 1,
        "type": "json_schema",
    }
    assert not any(
        row.get("event", {}).get("type", "").startswith("structured_output_")
        for row in rows
    )


def test_replay_rejects_structured_schema_mismatch_at_exact_path_before_events(
    tmp_path: Path,
    restore_api_registry,
    monkeypatch: pytest.MonkeyPatch,
    offline,
) -> None:
    recording_file = tmp_path / "schema-mismatch-v2.jsonl"
    capabilities = StructuredOutputCapabilities(
        native="supported",
        dialect="test",
        streaming=True,
        with_tools=False,
        schema_profile="none",
    )
    register_api_provider(
        _API,
        RecordingProvider(
            _scripted_with((ScriptedText('{"answer":2}'),)), recording_file
        ),
        capabilities,
    )
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", "off")
    _runtime_for_registered_model(monkeypatch).exec(
        "answer", response_format=_STRUCTURED_SCHEMA, toolset="none"
    )

    replay = ReplayProvider(recording_file)
    register_api_provider(_API, replay, capabilities)
    stream_module = importlib.import_module("openprogram.providers.stream")
    monkeypatch.setattr(
        stream_module,
        "resolve_provider_key",
        lambda provider: pytest.fail("schema mismatch resolved credentials"),
    )
    failover_module = importlib.import_module("openprogram.providers.utils.failover")
    monkeypatch.setattr(
        failover_module,
        "failover_stream_fn",
        lambda *args, **kwargs: pytest.fail("schema mismatch selected fallback"),
    )
    original_import = builtins.__import__

    def reject_vendor_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"anthropic", "boto3", "google", "openai"}:
            pytest.fail(f"schema mismatch imported vendor SDK {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_vendor_import)
    changed = {
        **_STRUCTURED_SCHEMA,
        "properties": {"answer": {"type": "string"}},
    }
    expected_path = "options.response_format.schema.properties.answer.type"
    for _ in range(2):
        with pytest.raises(LLMError, match=expected_path):
            _runtime_for_registered_model(monkeypatch, max_retries=3).exec(
                "answer", response_format=changed, toolset="none"
            )

    assert replay.call_count == 0


def test_replay_mismatch_display_is_bounded_escaped_and_omits_values() -> None:
    field_path = "options.response_format.schema.properties." + "x" * 5000
    recorded = {
        "description": "internal schema text",
        "type": "integer",
        "api_key": _SECRET,
    }
    incoming = None  # the object-valued schema key is omitted on this side

    mismatch = ReplayMismatch(0, field_path, recorded, incoming)
    message = str(mismatch)

    assert len(message) <= 512
    assert "internal schema text" not in message
    assert _SECRET not in message
    assert mismatch.field_path == field_path
    assert mismatch.recorded is recorded
    assert mismatch.incoming is incoming

    escaped = str(ReplayMismatch(0, "options.schema.bad\nkey", None, recorded))
    assert "\n" not in escaped
    assert "\\n" in escaped


def test_v1_structured_recording_is_rejected_explicitly(tmp_path: Path) -> None:
    recording_file = tmp_path / "structured-v1.jsonl"
    rows = [
        {"type": "header", "format_version": 1},
        {
            "type": "request",
            "call_index": 0,
            "model": _model().model_dump(mode="json"),
            "context": Context(messages=[]).model_dump(mode="json"),
            "options": {
                "response_format": {
                    "type": "json_schema",
                    "schema": _STRUCTURED_SCHEMA,
                }
            },
        },
        {"type": "call_end", "call_index": 0, "event_count": 0},
    ]
    recording_file.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    recording_file.chmod(0o600)

    with pytest.raises(RecordingFileError, match="version 1.*structured"):
        ReplayProvider(recording_file)


def test_v1_ordinary_recording_remains_replayable(
    tmp_path: Path, restore_api_registry
) -> None:
    recording_file = _record_one_call(tmp_path)
    rows = [
        json.loads(line)
        for line in recording_file.read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["format_version"] = 1
    request = next(row for row in rows if row["type"] == "request")
    assert request["options"].get("response_format") is None
    request["options"]["signal"] = None  # v1 serialized this request-local field
    request["options"]["on_payload"] = None  # v1 serialized this callback field
    recording_file.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    replay = ReplayProvider(recording_file)
    register_api_provider(_API, replay)
    events = _drain_stream(_model())
    assert events[-1].type == "done"
    assert replay.call_count == 1
    replay.assert_consumed()


def test_remove_secret_values_covers_nested_and_inline_secrets() -> None:
    """Field-name matching alone misses a token embedded in a free-form string."""
    cleaned = remove_secret_values(
        {
            "compat": {"extra": {"access_token": "t-1"}},
            "note": f"call with Bearer {_SECRET} please",
            "url": "https://host/v1?api_key=abcdef&x=1",
            "keep": ["plain", 3],
        }
    )

    assert cleaned["compat"]["extra"]["access_token"] == PLACEHOLDER
    assert cleaned["note"] == f"call with {PLACEHOLDER} please"
    assert cleaned["url"] == f"https://host/v1?api_key={PLACEHOLDER}&x=1"
    assert cleaned["keep"] == ["plain", 3]


def test_remove_secret_values_covers_auth_schemes_and_url_userinfo() -> None:
    cleaned = remove_secret_values(
        {
            "basic": "Basic dXNlcjpwYXNzd29yZA==",
            "bot": "Bot 123456789:ABCdef_ghi-jkl",
            "url": "https://alice:password@example.test/v1?access-token=value123&x=1",
        }
    )

    assert cleaned == {
        "basic": PLACEHOLDER,
        "bot": PLACEHOLDER,
        "url": f"https://{PLACEHOLDER}@example.test/v1?access-token={PLACEHOLDER}&x=1",
    }


def test_remove_secret_values_covers_compound_secret_field_suffixes() -> None:
    cleaned = remove_secret_values(
        {
            "X-Session-Token": "opaque-token-value",
            "service_api_key": "opaque-key-value",
            "client-secret": "opaque-secret-value",
            "database_password": "opaque-password-value",
            "refreshToken": "opaque-refresh-value",
            "accessToken": "opaque-access-value",
            "clientSecret": "opaque-client-value",
            "privateKey": "opaque-private-value",
            "token_count": 17,
            "tokenCount": 18,
            "monkey": "ordinary-value",
        }
    )

    assert cleaned == {
        "X-Session-Token": PLACEHOLDER,
        "service_api_key": PLACEHOLDER,
        "client-secret": PLACEHOLDER,
        "database_password": PLACEHOLDER,
        "refreshToken": PLACEHOLDER,
        "accessToken": PLACEHOLDER,
        "clientSecret": PLACEHOLDER,
        "privateKey": PLACEHOLDER,
        "token_count": 17,
        "tokenCount": 18,
        "monkey": "ordinary-value",
    }


def _run_tool_loop(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    monkeypatch.delenv("OPENPROGRAM_FALLBACK_MODELS", raising=False)
    stream_module = importlib.import_module("openprogram.providers.stream")
    monkeypatch.setattr(stream_module, "resolve_provider_key", lambda provider: None)

    class EmptyMemory:
        def search(self, query: str) -> str:
            return ""

    monkeypatch.setattr("openprogram.memory.get_backend", EmptyMemory)

    executed: list[tuple[str, dict]] = []

    async def echo(call_id, args, cancel_event, on_update) -> AgentToolResult:
        executed.append((call_id, args))
        return AgentToolResult(content=[TextContent(text=f"echo:{args['value']}")])

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Returns the supplied value.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        execute=echo,
    )
    config = AgentLoopConfig(
        model=_model(),
        convert_to_llm=lambda messages: [
            message
            for message in messages
            if getattr(message, "role", None) in {"user", "assistant", "toolResult"}
        ],
    )

    async def run():
        events = []
        async for event in agent_loop(
            [UserMessage(content="use echo", timestamp=0)],
            AgentContext(tools=[tool], memory_prefetch=""),
            config,
        ):
            events.append(event)
        return events

    return asyncio.run(run()), executed


def test_replay_reruns_a_multi_turn_tool_loop_without_network(
    tmp_path: Path, restore_api_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recording file that lost its second call would not finish the loop offline."""
    recording_file = tmp_path / "tool-loop.jsonl"
    scripted = _scripted_with(
        (ScriptedToolCall("echo", {"value": "hi"}, "call-1"),),
        (ScriptedText("tool completed"),),
    )
    register_api_provider(_API, RecordingProvider(scripted, recording_file))
    recorded_events, recorded_executions = _run_tool_loop(monkeypatch)

    replay = ReplayProvider(recording_file)
    register_api_provider(_API, replay)

    import socket

    real_connect = socket.socket.connect

    def refuse_non_loopback(sock, address, *args, **kwargs):
        if (
            os.name == "nt"
            and isinstance(address, tuple)
            and address
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return real_connect(sock, address, *args, **kwargs)
        pytest.fail("replay opened a network connection")

    monkeypatch.setattr(
        socket.socket,
        "connect",
        refuse_non_loopback,
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("replay opened a network connection"),
    )
    replayed_events, replayed_executions = _run_tool_loop(monkeypatch)

    assert replay.call_count == 2
    assert replayed_executions == recorded_executions == [("call-1", {"value": "hi"})]
    assert [event.type for event in replayed_events] == [
        event.type for event in recorded_events
    ]
    assert replayed_events[-1].messages[-1].content == [
        TextContent(text="tool completed")
    ]
    assert any(
        isinstance(message, ToolResultMessage)
        for message in replayed_events[-1].messages
    )


def test_replay_names_the_call_and_field_of_the_first_difference(
    tmp_path: Path, restore_api_registry, offline
) -> None:
    """A blanket 'replay failed' would not tell which field drifted."""
    recording_file = tmp_path / "drift.jsonl"
    register_api_provider(
        _API,
        RecordingProvider(_scripted_with((ScriptedText("first"),)), recording_file),
    )
    _drain_stream(_model())

    lines = [
        json.loads(raw)
        for raw in recording_file.read_text(encoding="utf-8").splitlines()
    ]
    for line in lines:
        if line["type"] == "request":
            line["context"]["messages"][0]["content"] = "goodbye"
    recording_file.write_text(
        "\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n",
        encoding="utf-8",
    )

    register_api_provider(_API, ReplayProvider(recording_file))
    with pytest.raises(ReplayMismatch) as caught:
        _drain_stream(_model())

    mismatch = caught.value
    assert mismatch.call_index == 0
    assert mismatch.field_path == "context.messages[0].content"
    assert mismatch.recorded == "goodbye"
    assert mismatch.incoming == "hello"
    assert "context.messages[0].content" in str(mismatch)


def test_replay_mismatch_does_not_consume_the_recorded_call(tmp_path: Path) -> None:
    recording_file = _record_one_call(tmp_path)
    replay = ReplayProvider(recording_file)
    wrong = Context(messages=[UserMessage(content="wrong", timestamp=0)])
    right = Context(messages=[UserMessage(content="hello", timestamp=0)])

    async def drain(context: Context) -> list[AssistantMessageEvent]:
        return [
            event async for event in replay.stream_simple(_model(), context, _options())
        ]

    with pytest.raises(ReplayMismatch):
        asyncio.run(drain(wrong))
    assert replay.call_count == 0
    assert asyncio.run(drain(right))[-1].type == "done"
    assert replay.call_count == 1


def test_recording_finalizes_and_closes_source_when_provider_raises(
    tmp_path: Path,
) -> None:
    source = _InterruptingProvider(fail_after_first=True)
    recording_file = tmp_path / "provider-error.jsonl"
    recorder = RecordingProvider(source, recording_file)

    async def drain() -> None:
        async for _ in recorder.stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hello", timestamp=0)]),
            _options(),
        ):
            pass

    with pytest.raises(RuntimeError, match="provider disconnected"):
        asyncio.run(drain())

    calls = read_recording_file(recording_file)
    assert source.closed
    assert calls[0].outcome == "error"
    replay = ReplayProvider(recording_file)

    async def replay_interrupted() -> None:
        async for _ in replay.stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hello", timestamp=0)]),
            _options(),
        ):
            pass

    with pytest.raises(RecordingFileError, match="recorded call ended with error"):
        asyncio.run(replay_interrupted())
    assert replay.call_count == 1


@pytest.mark.parametrize(
    "entry, message",
    [
        ("stream", "stream start failed"),
        ("stream_simple", "stream_simple start failed"),
    ],
)
def test_recording_finalizes_when_provider_source_construction_raises(
    tmp_path: Path,
    entry: str,
    message: str,
) -> None:
    recording_file = tmp_path / f"{entry}-start-error.jsonl"
    recorder = RecordingProvider(_SyncStartFailureProvider(), recording_file)

    async def drain() -> None:
        source = getattr(recorder, entry)(
            _model(),
            Context(messages=[UserMessage(content="hello", timestamp=0)]),
            _options(),
        )
        async for _ in source:
            pass

    with pytest.raises(RuntimeError, match=message):
        asyncio.run(drain())

    calls = read_recording_file(recording_file)
    assert len(calls) == 1
    assert calls[0].outcome == "error"


def test_recording_stops_after_the_first_terminal_event(tmp_path: Path) -> None:
    source = _DuplicateTerminalProvider()
    recording_file = tmp_path / "duplicate-terminal.jsonl"
    recorder = RecordingProvider(source, recording_file)

    async def drain() -> list[AssistantMessageEvent]:
        return [
            event
            async for event in recorder.stream_simple(
                _model(),
                Context(messages=[UserMessage(content="hello", timestamp=0)]),
                _options(),
            )
        ]

    events = asyncio.run(drain())
    calls = read_recording_file(recording_file)
    rows = [
        json.loads(line)
        for line in recording_file.read_text(encoding="utf-8").splitlines()
    ]
    assert source.closed
    assert sum(row["type"] == "call_end" for row in rows) == 1
    assert sum(event.type == "done" for event in events) == 1
    assert calls[0].outcome == "complete"


def test_recording_preserves_source_close_failure_after_terminal(
    tmp_path: Path,
) -> None:
    recording_file = tmp_path / "terminal-close-error.jsonl"
    recorder = RecordingProvider(_TerminalCloseFailureProvider(), recording_file)

    async def drain() -> None:
        async for _ in recorder.stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hello", timestamp=0)]),
            _options(),
        ):
            pass

    with pytest.raises(RuntimeError, match="source close failed"):
        asyncio.run(drain())
    calls = read_recording_file(recording_file)
    rows = [
        json.loads(line)
        for line in recording_file.read_text(encoding="utf-8").splitlines()
    ]
    assert calls[0].outcome == "complete"
    assert sum(row["type"] == "call_end" for row in rows) == 1


def test_recording_finalizes_and_closes_source_when_consumer_abandons(
    tmp_path: Path,
) -> None:
    source = _InterruptingProvider()
    recording_file = tmp_path / "consumer-close.jsonl"
    recorder = RecordingProvider(source, recording_file)

    async def abandon() -> None:
        stream = recorder.stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hello", timestamp=0)]),
            _options(),
        )
        await anext(stream)
        await stream.aclose()

    asyncio.run(abandon())
    calls = read_recording_file(recording_file)
    assert source.closed
    assert calls[0].outcome == "abandoned"


def test_recording_finalizes_and_closes_source_when_cancelled(tmp_path: Path) -> None:
    source = _InterruptingProvider()
    recording_file = tmp_path / "cancelled.jsonl"
    recorder = RecordingProvider(source, recording_file)

    async def cancel() -> None:
        stream = recorder.stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hello", timestamp=0)]),
            _options(),
        )
        await anext(stream)
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    calls = read_recording_file(recording_file)
    assert source.closed
    assert calls[0].outcome == "cancelled"


def test_replay_refuses_a_recording_file_written_in_another_format_version(
    tmp_path: Path,
) -> None:
    """Serving a foreign recording file would replay events whose shape is not guaranteed."""
    recording_file = tmp_path / "old.jsonl"
    recording_file.write_text(
        json.dumps({"type": "header", "format_version": RECORDING_FORMAT_VERSION + 1})
        + "\n",
        encoding="utf-8",
    )
    recording_file.chmod(0o600)

    with pytest.raises(RecordingFileError) as caught:
        ReplayProvider(recording_file)

    assert str(RECORDING_FORMAT_VERSION + 1) in str(caught.value)
    assert str(RECORDING_FORMAT_VERSION) in str(caught.value)


def test_replay_refuses_a_recording_file_without_a_format_header(
    tmp_path: Path,
) -> None:
    """A headerless file has no version to check at all."""
    recording_file = tmp_path / "headerless.jsonl"
    recording_file.write_text(
        json.dumps({"type": "request", "call_index": 0}) + "\n", encoding="utf-8"
    )
    recording_file.chmod(0o600)

    with pytest.raises(RecordingFileError, match="no format header"):
        ReplayProvider(recording_file)


def _record_one_call(tmp_path: Path) -> Path:
    recording_file = tmp_path / "strict.jsonl"
    provider = RecordingProvider(
        _scripted_with((ScriptedText("done"),)), recording_file
    )

    async def run() -> None:
        async for _ in provider.stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hello", timestamp=0)]),
            _options(),
        ):
            pass

    asyncio.run(run())
    return recording_file


@pytest.mark.parametrize(
    "mutation, match",
    [
        (lambda rows: rows.insert(1, dict(rows[0])), "duplicate header"),
        (
            lambda rows: rows.__setitem__(-1, {**rows[-1], "event_count": 99}),
            "event_count",
        ),
        (lambda rows: rows.pop(), "missing call_end"),
        (lambda rows: rows.append({"type": "mystery"}), "unknown row type"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "call_index": 1}), "contiguous"),
        (
            lambda rows: rows.__setitem__(-1, {**rows[-1], "outcome": "unknown"}),
            "invalid call_end outcome",
        ),
    ],
)
def test_replay_strictly_validates_structure(
    tmp_path: Path, mutation, match: str
) -> None:
    recording_file = _record_one_call(tmp_path)
    rows = [
        json.loads(line)
        for line in recording_file.read_text(encoding="utf-8").splitlines()
    ]
    mutation(rows)
    recording_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    with pytest.raises(RecordingFileError, match=match):
        ReplayProvider(recording_file)


def test_replay_prevalidates_event_types(tmp_path: Path) -> None:
    recording_file = _record_one_call(tmp_path)
    rows = [
        json.loads(line)
        for line in recording_file.read_text(encoding="utf-8").splitlines()
    ]
    event = next(row for row in rows if row["type"] == "event")
    event["event"]["type"] = "unknown_event"
    recording_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    with pytest.raises(RecordingFileError, match="unknown event type"):
        ReplayProvider(recording_file)


@_POSIX_PERMISSION_AND_SYMLINK
def test_recording_file_and_parent_are_private(tmp_path: Path) -> None:
    parent = tmp_path / "recordings"
    recording_file = parent / "private.jsonl"
    RecordingProvider(_scripted_with((ScriptedText("done"),)), recording_file)

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(recording_file.stat().st_mode) == 0o600


@_POSIX_PERMISSION_AND_SYMLINK
def test_recording_tightens_preexisting_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "recordings"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    RecordingProvider(_scripted_with((ScriptedText("done"),)), parent / "private.jsonl")

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


@_POSIX_PERMISSION_AND_SYMLINK
def test_recording_preserves_external_parent_permissions(tmp_path: Path) -> None:
    parent = tmp_path / "shared-project"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)

    RecordingProvider(_scripted_with((ScriptedText("done"),)), parent / "private.jsonl")

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755


@_POSIX_PERMISSION_AND_SYMLINK
def test_recording_does_not_chmod_external_parent_symlink(tmp_path: Path) -> None:
    target = tmp_path / "shared-target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    link = tmp_path / "shared-link"
    link.symlink_to(target, target_is_directory=True)

    RecordingProvider(_scripted_with((ScriptedText("done"),)), link / "private.jsonl")

    assert stat.S_IMODE(target.stat().st_mode) == 0o755


@_POSIX_PERMISSION_AND_SYMLINK
def test_recording_rejects_symlinked_managed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "shared-target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    (state / "recordings").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)

    with pytest.raises(PermissionError, match="must not be a symlink"):
        RecordingProvider(
            _scripted_with((ScriptedText("done"),)),
            state / "recordings" / "private.jsonl",
        )

    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert not (target / "private.jsonl").exists()


@_POSIX_PERMISSION_AND_SYMLINK
def test_symlinked_managed_root_does_not_block_external_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    managed_target = tmp_path / "managed-target"
    managed_target.mkdir(mode=0o755)
    (state / "recordings").symlink_to(managed_target, target_is_directory=True)
    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    external.chmod(0o755)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)

    RecordingProvider(
        _scripted_with((ScriptedText("done"),)), external / "private.jsonl"
    )

    assert (external / "private.jsonl").is_file()
    assert stat.S_IMODE(external.stat().st_mode) == 0o755


@_POSIX_PERMISSION_AND_SYMLINK
def test_dotdot_cannot_bypass_symlinked_managed_root_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "x").mkdir()
    target = tmp_path / "shared-target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    (state / "recordings").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)
    disguised = state / "x" / ".." / "recordings" / "private.jsonl"

    with pytest.raises(PermissionError, match="must not be a symlink"):
        RecordingProvider(_scripted_with((ScriptedText("done"),)), disguised)

    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert not (target / "private.jsonl").exists()


@_POSIX_PERMISSION_AND_SYMLINK
def test_case_alias_cannot_bypass_symlinked_managed_root_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "shared-target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    (state / "recordings").symlink_to(target, target_is_directory=True)
    alias = state / "RECORDINGS"
    if not alias.exists():
        pytest.skip("filesystem is case-sensitive")
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)

    with pytest.raises(PermissionError, match="must not be a symlink"):
        RecordingProvider(
            _scripted_with((ScriptedText("done"),)), alias / "private.jsonl"
        )

    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert not (target / "private.jsonl").exists()


@_POSIX_PERMISSION_AND_SYMLINK
def test_direct_managed_symlink_target_remains_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "shared-target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    (state / "recordings").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)

    RecordingProvider(_scripted_with((ScriptedText("done"),)), target / "direct.jsonl")

    assert (target / "direct.jsonl").is_file()
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


@_POSIX_PERMISSION_AND_SYMLINK
def test_recording_rejects_intermediate_symlink_inside_managed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    managed_root = state / "recordings"
    managed_root.mkdir(parents=True, mode=0o700)
    target = tmp_path / "shared-target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    (managed_root / "nested").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)

    with pytest.raises(PermissionError, match="must not contain a symlink"):
        RecordingProvider(
            _scripted_with((ScriptedText("done"),)),
            managed_root / "nested" / "private.jsonl",
        )

    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert not (target / "private.jsonl").exists()


@_POSIX_PERMISSION_AND_SYMLINK
def test_replay_rejects_intermediate_symlink_without_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    managed_root = state / "recordings"
    managed_root.mkdir(parents=True, mode=0o700)
    external = tmp_path / "external"
    recording_file = _record_one_call(external)
    recording_file.chmod(0o644)
    (managed_root / "nested").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)

    with pytest.raises(RecordingFileError, match="must not contain a symlink"):
        ReplayProvider(managed_root / "nested" / recording_file.name)

    assert stat.S_IMODE(recording_file.stat().st_mode) == 0o644


@_POSIX_PERMISSION_AND_SYMLINK
def test_external_replay_rejects_wide_permissions_without_chmod(tmp_path: Path) -> None:
    recording_file = _record_one_call(tmp_path)
    recording_file.chmod(0o644)

    with pytest.raises(RecordingFileError, match="mode 0600"):
        ReplayProvider(recording_file)

    assert stat.S_IMODE(recording_file.stat().st_mode) == 0o644


@_POSIX_PERMISSION_AND_SYMLINK
def test_managed_replay_repairs_wide_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed_root = tmp_path / "recordings"
    recording_file = _record_one_call(managed_root)
    recording_file.chmod(0o644)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)

    ReplayProvider(recording_file)

    assert stat.S_IMODE(recording_file.stat().st_mode) == 0o600


def test_replay_stream_skips_credential_resolution(
    tmp_path: Path,
    restore_api_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_file = tmp_path / "offline.jsonl"
    context = Context(messages=[UserMessage(content="hello", timestamp=0)])
    options = SimpleStreamOptions()
    recorder = RecordingProvider(
        _scripted_with((ScriptedText("done"),)), recording_file
    )

    async def record() -> None:
        async for _ in recorder.stream_simple(_model(), context, options):
            pass

    asyncio.run(record())
    replay = ReplayProvider(recording_file)
    register_api_provider(_API, replay)
    stream_module = importlib.import_module("openprogram.providers.stream")
    monkeypatch.setattr(
        stream_module,
        "resolve_provider_key",
        lambda provider: pytest.fail("replay resolved provider credentials"),
    )

    async def replay_once() -> list:
        return [
            event
            async for event in stream_module.stream_simple(_model(), context, options)
        ]

    assert [event.type for event in asyncio.run(replay_once())][-1] == "done"
    replay.assert_consumed()


def test_replay_assert_consumed_reports_remaining_calls(tmp_path: Path) -> None:
    replay = ReplayProvider(_record_one_call(tmp_path))

    with pytest.raises(ReplayMismatch, match="unconsumed") as caught:
        replay.assert_consumed()

    assert caught.value.call_index == 0
