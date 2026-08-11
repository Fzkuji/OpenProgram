"""Recording and offline replay of provider calls.

The recorder wraps the scripted provider (standing in for a real network
provider) at the API-registry chokepoint; the replayer serves the resulting
recording file back with no provider underneath it at all.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from openprogram.agent.agent_loop import agent_loop
from openprogram.agent.types import AgentContext, AgentLoopConfig, AgentTool, AgentToolResult
from openprogram.providers.api_registry import get_api_provider, register_api_provider
from openprogram.providers.recording import (
    PLACEHOLDER,
    RECORDING_FORMAT_VERSION,
    RecordingProvider,
    remove_secret_values,
)
from openprogram.providers.replay import ReplayMismatch, ReplayProvider, RecordingFileError
from openprogram.providers.types import (
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from tests.providers.scripted_provider import ScriptedText, ScriptedToolCall


_API = "record-replay-test-api"
_SECRET = "sk-livekey0123456789abcdef"


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
    else:
        register_api_provider(_API, previous)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything under test opens a socket."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("replay must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _scripted_with(*responses) -> object:
    from tests.providers.scripted_provider import ScriptedProvider

    provider = ScriptedProvider()
    for steps in responses:
        provider.add_response(*steps)
    return provider


def _drain_stream(model: Model, options: SimpleStreamOptions | None = None) -> list:
    from openprogram.providers import stream_simple

    async def run():
        return [
            event async for event in stream_simple(
                model, Context(messages=[UserMessage(content="hello", timestamp=0)]),
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
    assert lines[0] == {"type": "header", "format_version": RECORDING_FORMAT_VERSION}
    request = next(line for line in lines if line["type"] == "request")
    assert request["options"]["api_key"] == PLACEHOLDER
    assert request["options"]["headers"]["Cookie"] == PLACEHOLDER
    assert request["model"]["headers"]["Authorization"] == PLACEHOLDER
    assert [line["type"] for line in lines[-2:]] == ["event", "call_end"]


def test_remove_secret_values_covers_nested_and_inline_secrets() -> None:
    """Field-name matching alone misses a token embedded in a free-form string."""
    cleaned = remove_secret_values({
        "compat": {"extra": {"access_token": "t-1"}},
        "note": f"call with Bearer {_SECRET} please",
        "url": "https://host/v1?api_key=abcdef&x=1",
        "keep": ["plain", 3],
    })

    assert cleaned["compat"]["extra"]["access_token"] == PLACEHOLDER
    assert cleaned["note"] == f"call with {PLACEHOLDER} please"
    assert cleaned["url"] == f"https://host/v1?api_key={PLACEHOLDER}&x=1"
    assert cleaned["keep"] == ["plain", 3]


def test_remove_secret_values_covers_auth_schemes_and_url_userinfo() -> None:
    cleaned = remove_secret_values({
        "basic": "Basic dXNlcjpwYXNzd29yZA==",
        "bot": "Bot 123456789:ABCdef_ghi-jkl",
        "url": "https://alice:password@example.test/v1?access-token=value123&x=1",
    })

    assert cleaned == {
        "basic": PLACEHOLDER,
        "bot": PLACEHOLDER,
        "url": f"https://{PLACEHOLDER}@example.test/v1?access-token={PLACEHOLDER}&x=1",
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
            message for message in messages
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
    monkeypatch.setattr(
        socket.socket, "connect",
        lambda *args, **kwargs: pytest.fail("replay opened a network connection"),
    )
    monkeypatch.setattr(
        socket, "create_connection",
        lambda *args, **kwargs: pytest.fail("replay opened a network connection"),
    )
    replayed_events, replayed_executions = _run_tool_loop(monkeypatch)

    assert replay.call_count == 2
    assert replayed_executions == recorded_executions == [("call-1", {"value": "hi"})]
    assert [event.type for event in replayed_events] == [
        event.type for event in recorded_events
    ]
    assert replayed_events[-1].messages[-1].content == [TextContent(text="tool completed")]
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
        _API, RecordingProvider(_scripted_with((ScriptedText("first"),)), recording_file)
    )
    _drain_stream(_model())

    lines = [json.loads(raw) for raw in recording_file.read_text(encoding="utf-8").splitlines()]
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


def test_replay_refuses_a_recording_file_written_in_another_format_version(
    tmp_path: Path,
) -> None:
    """Serving a foreign recording file would replay events whose shape is not guaranteed."""
    recording_file = tmp_path / "old.jsonl"
    recording_file.write_text(
        json.dumps({"type": "header", "format_version": RECORDING_FORMAT_VERSION + 1}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordingFileError) as caught:
        ReplayProvider(recording_file)

    assert str(RECORDING_FORMAT_VERSION + 1) in str(caught.value)
    assert str(RECORDING_FORMAT_VERSION) in str(caught.value)


def test_replay_refuses_a_recording_file_without_a_format_header(tmp_path: Path) -> None:
    """A headerless file has no version to check at all."""
    recording_file = tmp_path / "headerless.jsonl"
    recording_file.write_text(json.dumps({"type": "request", "call_index": 0}) + "\n", encoding="utf-8")

    with pytest.raises(RecordingFileError, match="no format header"):
        ReplayProvider(recording_file)


def _record_one_call(tmp_path: Path) -> Path:
    recording_file = tmp_path / "strict.jsonl"
    provider = RecordingProvider(_scripted_with((ScriptedText("done"),)), recording_file)

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
        (lambda rows: rows.__setitem__(-1, {**rows[-1], "event_count": 99}), "event_count"),
        (lambda rows: rows.pop(), "missing call_end"),
        (lambda rows: rows.append({"type": "mystery"}), "unknown row type"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "call_index": 1}), "contiguous"),
    ],
)
def test_replay_strictly_validates_structure(tmp_path: Path, mutation, match: str) -> None:
    recording_file = _record_one_call(tmp_path)
    rows = [json.loads(line) for line in recording_file.read_text().splitlines()]
    mutation(rows)
    recording_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    with pytest.raises(RecordingFileError, match=match):
        ReplayProvider(recording_file)


def test_replay_prevalidates_event_types(tmp_path: Path) -> None:
    recording_file = _record_one_call(tmp_path)
    rows = [json.loads(line) for line in recording_file.read_text().splitlines()]
    event = next(row for row in rows if row["type"] == "event")
    event["event"]["type"] = "unknown_event"
    recording_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    with pytest.raises(RecordingFileError, match="unknown event type"):
        ReplayProvider(recording_file)


def test_recording_file_and_parent_are_private(tmp_path: Path) -> None:
    parent = tmp_path / "recordings"
    recording_file = parent / "private.jsonl"
    RecordingProvider(_scripted_with((ScriptedText("done"),)), recording_file)

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(recording_file.stat().st_mode) == 0o600
