import asyncio
import threading
import time

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.structured_output import (
    StructuredOutputSchemaError,
    StructuredOutputValidationError,
    StructuredOutputUnsupportedError,
)
from openprogram.providers.types import (
    AssistantMessage,
    EventDone,
    EventError,
    EventStart,
    Model,
    TextContent,
)
from openprogram.providers.utils.errors import ExecInterrupt


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_callable_runtime_returns_validated_python_value_and_forwards_schema():
    seen = []

    def call(content, model="test", response_format=None):
        seen.append(response_format)
        return '{"answer": 7}'

    result = Runtime(call=call, model="dummy").exec("question", response_format=SCHEMA)

    assert result == {"answer": 7}
    assert seen == [SCHEMA]


def test_invalid_schema_fails_before_callable_runs():
    calls = []
    runtime = Runtime(call=lambda *args, **kwargs: calls.append(1), model="dummy")

    with pytest.raises(StructuredOutputSchemaError):
        runtime.exec("question", response_format={"type": "not-a-type"})

    assert calls == []


def test_invalid_result_is_not_retried_as_transport_failure():
    calls = []

    def call(content, model="test", response_format=None):
        calls.append(1)
        return '{"answer": "wrong"}'

    runtime = Runtime(call=call, model="dummy", max_retries=3)
    response_format = {
        "type": "json_schema",
        "schema": SCHEMA,
        "max_validation_retries": 0,
    }
    with pytest.raises(StructuredOutputValidationError) as exc:
        runtime.exec("question", response_format=response_format)

    assert exc.value.code == "validation_failed"
    assert calls == [1]


def test_validation_failure_gets_one_bounded_semantic_repair():
    seen_content = []

    def call(content, model="test", response_format=None):
        seen_content.append(content)
        if len(seen_content) == 1:
            return '{"answer": "wrong"}'
        return '{"answer": 11}'

    result = Runtime(call=call, model="dummy", max_retries=3).exec(
        "question", response_format=SCHEMA
    )

    assert result == {"answer": 11}
    assert len(seen_content) == 2
    repair_text = seen_content[1][-1]["text"]
    assert "validation_failed" in repair_text
    assert len(repair_text) < 4000


def test_validation_repair_is_owned_by_one_agent_session(monkeypatch):
    seen_content = []
    session_runs = []

    def call(content, model="test", response_format=None):
        seen_content.append(content)
        return '{"answer":"wrong"}' if len(seen_content) == 1 else '{"answer":12}'

    from openprogram.agent.session import AgentSession

    original_run = AgentSession.run

    async def counted_run(self, *args, **kwargs):
        session_runs.append(self)
        return await original_run(self, *args, **kwargs)

    monkeypatch.setattr(AgentSession, "run", counted_run)
    stream_events = []
    runtime = Runtime(call=call, model="dummy", max_retries=3)
    runtime.on_stream = stream_events.append
    result = runtime.exec(
        "question",
        response_format=SCHEMA,
    )

    assert result == {"answer": 12}
    assert len(session_runs) == 1
    assert len(seen_content) == 2
    persisted = [
        message for message in session_runs[0]._agent.state.messages
        if message.role == "assistant"
    ]
    assert len(persisted) == 1
    assert persisted[0].structured_output == {"answer": 12}
    assert persisted[0].structured_output_attempt == 2
    assert [event["type"] for event in stream_events if event["type"].startswith("structured_")] == [
        "structured_output_retry",
        "structured_output_end",
    ]


def test_sync_and_async_exec_share_agent_owned_typed_lifecycle():
    sync_calls = []
    async_calls = []

    def sync_call(content, model="test", response_format=None):
        sync_calls.append(content)
        return '{"answer":21}'

    async def async_call(content, model="test", response_format=None):
        async_calls.append(content)
        return '{"answer":21}'

    sync_value = Runtime(call=sync_call, model="dummy").exec(
        "question", response_format=SCHEMA
    )
    async_value = asyncio.run(
        Runtime(call=async_call, model="dummy").async_exec(
            "question", response_format=SCHEMA
        )
    )

    assert sync_value == async_value == {"answer": 21}
    assert len(sync_calls) == len(async_calls) == 1


def test_structured_json_null_is_a_valid_typed_result():
    runtime = Runtime(
        call=lambda content, model="test", response_format=None: "null",
        model="dummy",
    )

    assert runtime.exec("question", response_format={"type": "null"}) is None


def test_public_runtime_aborted_structured_stream_propagates_once_without_persistence(
    monkeypatch,
):
    calls = []
    sessions = []

    async def aborted_stream(model, context, options=None):
        calls.append(1)
        message = AssistantMessage(
            content=[TextContent(text="partial")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            stop_reason="aborted",
            timestamp=int(time.time() * 1000),
        )
        yield EventStart(partial=message)
        yield EventError(reason="aborted", error=message)

    from openprogram.agent.session import AgentSession

    original_run = AgentSession.run

    async def capture_session(self, *args, **kwargs):
        sessions.append(self)
        return await original_run(self, *args, **kwargs)

    monkeypatch.setattr(AgentSession, "run", capture_session)
    stream_events = []
    runtime = Runtime(call=lambda *args, **kwargs: "unused", model="dummy", max_retries=3)
    runtime.on_stream = stream_events.append

    with pytest.raises(ExecInterrupt, match="aborted"):
        runtime.exec("question", response_format=SCHEMA, stream_fn=aborted_stream)

    assert calls == [1]
    assert len(sessions) == 1
    assert not any(
        message.role == "assistant" for message in sessions[0]._agent.state.messages
    )
    assert not any(
        event["type"].startswith("structured_") for event in stream_events
    )


def test_ordinary_async_callable_preserves_direct_baseline_without_agent_session(
    monkeypatch,
):
    seen_content = []
    session_runs = []

    async def call(content, model="test", response_format=None):
        seen_content.append(content)
        return "ordinary reply"

    from openprogram.agent.session import AgentSession

    original_run = AgentSession.run

    async def counted_run(self, *args, **kwargs):
        session_runs.append(self)
        return await original_run(self, *args, **kwargs)

    monkeypatch.setattr(AgentSession, "run", counted_run)
    stream_events = []
    runtime = Runtime(call=call, model="dummy")
    runtime.on_stream = stream_events.append

    result = asyncio.run(runtime.async_exec("hello"))

    assert result == "ordinary reply"
    assert seen_content == [[{"type": "text", "text": "hello"}]]
    assert session_runs == []
    assert stream_events == []


def test_async_exec_cancellation_stops_before_structured_repair_and_waits_cleanup(
    monkeypatch,
):
    first_started = threading.Event()
    release_first = threading.Event()
    repair_started = threading.Event()
    session_finished = threading.Event()
    calls = []
    sessions = []

    def call(content, model="test", response_format=None):
        calls.append(content)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
            return '{"answer":"wrong"}'
        repair_started.set()
        return '{"answer":9}'

    from openprogram.agent.session import AgentSession

    original_run = AgentSession.run

    async def capture_session(self, *args, **kwargs):
        sessions.append(self)
        try:
            return await original_run(self, *args, **kwargs)
        finally:
            session_finished.set()

    monkeypatch.setattr(AgentSession, "run", capture_session)
    stream_events = []
    closed_nodes = []
    runtime = Runtime(call=call, model="dummy", max_retries=3)
    runtime.on_stream = stream_events.append
    original_close = runtime._close_model_call_node

    def capture_close(node_id, **kwargs):
        closed_nodes.append(kwargs.get("status", "completed"))
        return original_close(node_id, **kwargs)

    monkeypatch.setattr(runtime, "_close_model_call_node", capture_close)

    async def scenario():
        task = asyncio.create_task(
            runtime.async_exec("question", response_format=SCHEMA)
        )
        assert await asyncio.to_thread(first_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release_first.set()
        assert await asyncio.to_thread(session_finished.wait, 1)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())

    assert len(calls) == 1
    assert not repair_started.is_set()
    assert len(sessions) == 1
    assert not any(
        message.role == "assistant" for message in sessions[0]._agent.state.messages
    )
    assert not any(
        event["type"] in ("structured_output_retry", "structured_output_end", "done")
        for event in stream_events
    )
    assert "completed" not in closed_nodes


def test_async_exec_returns_validated_python_value():
    async def call(content, model="test", response_format=None):
        return '{"answer": 9}'

    runtime = Runtime(call=call, model="dummy")
    assert asyncio.run(runtime.async_exec("question", response_format=SCHEMA)) == {"answer": 9}


def test_unknown_provider_is_rejected_before_stream_call():
    calls = []
    retries = []

    async def stream(model, context, options=None):
        calls.append(1)
        if False:
            yield None

    runtime = Runtime(call=lambda *args, **kwargs: "unused", model="dummy", max_retries=3)
    runtime.api_model = Model(
        id="third-party-test",
        name="Third-party test",
        api="openai-completions",
        provider="openrouter",
        base_url="https://example.invalid",
    )

    with pytest.raises(StructuredOutputUnsupportedError):
        runtime.exec(
            "question",
            response_format=SCHEMA,
            stream_fn=stream,
            on_retry=lambda info: retries.append(info),
        )
    assert calls == []
    assert retries == []


def test_explicit_parallel_true_reaches_hidden_tool_preflight():
    calls = []

    async def stream(model, context, options=None):
        calls.append(options)
        if False:
            yield None

    runtime = Runtime(call=lambda *args, **kwargs: "unused", model="dummy", max_retries=1)
    runtime.api_model = Model(
        id="codex-test",
        name="Codex test",
        api="openai-codex",
        provider="openai-codex",
        base_url="https://example.invalid",
    )

    with pytest.raises(StructuredOutputUnsupportedError):
        runtime.exec(
            "question",
            response_format={
                "type": "json_schema",
                "schema": SCHEMA,
                "max_validation_retries": 0,
            },
            toolset="none",
            parallel_tool_calls=True,
            stream_fn=stream,
        )

    assert calls == []


def test_explicit_prompt_fallback_adds_schema_instruction():
    seen = {}

    async def stream(model, context, options=None):
        seen["system"] = context.system_prompt
        message = AssistantMessage(
            content=[TextContent(text='{"answer": 5}')],
            api=model.api,
            provider=model.provider,
            model=model.id,
            timestamp=int(time.time() * 1000),
        )
        yield EventStart(partial=message)
        yield EventDone(reason="stop", message=message)

    runtime = Runtime(call=lambda *args, **kwargs: "unused", model="dummy")
    runtime.api_model = Model(
        id="third-party-test",
        name="Third-party test",
        api="openai-completions",
        provider="openrouter",
        base_url="https://example.invalid",
    )
    response_format = {
        "type": "json_schema",
        "schema": SCHEMA,
        "fallback": "prompt",
        "max_validation_retries": 0,
    }

    result = runtime.exec("question", response_format=response_format, stream_fn=stream)

    assert result == {"answer": 5}
    assert "Return only one complete JSON value" in seen["system"]
    assert '"additionalProperties":false' in seen["system"]
