"""Public Runtime/AgentSession recovery must share failure accounting."""
import asyncio

import pytest

from openprogram.runtime.agent.loop.session import AgentSession
from openprogram.runtime.agent.runtime import Runtime
from openprogram.models.providers.structured_output import StructuredOutputValidationError
from openprogram.models.providers.types import AssistantMessage, EventDone, EventStart, TextContent, ToolCall
from openprogram.models.providers.utils.recovery import current_recovery

SCHEMA = {"type": "object", "properties": {"answer": {"type": "integer"}},
          "required": ["answer"], "additionalProperties": False}
ERROR = "Error Code None: Internal error during token generation"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr("openprogram.runtime.agent.loop.session.compute_backoff_ms", lambda *a: 0)
    monkeypatch.setattr("openprogram.runtime.agent.runtime._retry_sleep_seconds", lambda *a: 0)


def _response(model, content, n):
    return AssistantMessage(content=content, api=model.api, provider=model.provider,
                            model=model.id, timestamp=n,
                            stop_reason="toolUse" if isinstance(content[0], ToolCall) else "stop")


def test_runtime_session_transport_retries_leave_the_repair_allowance():
    calls, states = [], []

    async def stream(model, context, options=None):
        calls.append(1)
        states.append(current_recovery.get())
        if len(calls) <= 2:
            raise RuntimeError(ERROR)
        msg = _response(model, [TextContent(text="invalid JSON")], len(calls))
        yield EventStart(partial=msg)
        yield EventDone(reason="stop", message=msg)

    runtime = Runtime(call=lambda *a, **k: "unused", model="dummy", max_retries=3)
    with pytest.raises(StructuredOutputValidationError, match="not valid JSON"):
        runtime.exec("Return an answer", stream_fn=stream,
                     tools=[{"spec": {"name": "lookup", "description": "Read",
                                      "parameters": {"type": "object", "properties": {}}},
                             "execute": lambda: "evidence"}],
                     response_format={"type": "json_schema", "schema": SCHEMA, "fallback": "prompt"})
    state = states[-1]
    # Two reconnects, then the first answer plus both repairs the
    # allowance grants: the dropped connections cost no repair.
    assert len(calls) == 2 + 1 + state.limit
    assert state.used == state.limit
    assert state.transport_reserves == 2
    reasons = [a["reason"] for a in state.attempts if a["kind"] == "reserve"]
    assert reasons[:2] == ["transport", "transport"]
    assert "transport" not in reasons[2:]


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("failures", [1, 4])
def test_runtime_session_preserves_completed_tools_through_transport_retries(structured, failures):
    calls, effects, states = [], [], []

    async def stream(model, context, options=None):
        receipts = [m.tool_call_id for m in context.messages if m.role == "toolResult"]
        calls.append(receipts)
        states.append(current_recovery.get())
        n = len(calls)
        if 2 <= n <= failures + 1:
            raise RuntimeError(ERROR)
        content = ([TextContent(text='{"answer":7}')] if receipts else
                   [ToolCall(id=f"lookup-{n}", name="lookup", arguments={})])
        msg = _response(model, content, n)
        yield EventStart(partial=msg)
        yield EventDone(reason=msg.stop_reason, message=msg)

    # A small repair allowance does not cap reconnects.
    runtime = Runtime(call=lambda *a, **k: "unused", model="dummy", max_retries=2)
    kwargs = dict(stream_fn=stream,
                  tools=[{"spec": {"name": "lookup", "description": "Read",
                                   "parameters": {"type": "object", "properties": {}}},
                          "execute": lambda: effects.append(1) or "evidence"}],
                  response_format={"type": "json_schema", "schema": SCHEMA, "fallback": "prompt"})
    if not structured:
        kwargs.pop("response_format")
    expected = {"answer": 7} if structured else '{"answer":7}'
    assert runtime.exec("Read once then answer", **kwargs) == expected
    assert calls == [[]] + [["lookup-1"]] * (failures + 1)
    assert effects == [1]
    assert states[-1].used == 0
    assert states[-1].transport_reserves == failures
    assert states[-1].transport_used == 0


@pytest.mark.parametrize("structured", [False, True])
def test_runtime_session_stops_when_reconnects_run_out_and_keeps_the_error(structured):
    calls, effects, states = [], [], []

    async def stream(model, context, options=None):
        receipts = [m.tool_call_id for m in context.messages if m.role == "toolResult"]
        calls.append(receipts)
        states.append(current_recovery.get())
        if len(calls) >= 2:
            raise RuntimeError(ERROR)
        msg = _response(model, [ToolCall(id="lookup-1", name="lookup", arguments={})], 1)
        yield EventStart(partial=msg)
        yield EventDone(reason=msg.stop_reason, message=msg)

    runtime = Runtime(call=lambda *a, **k: "unused", model="dummy", max_retries=3)
    kwargs = dict(stream_fn=stream,
                  tools=[{"spec": {"name": "lookup", "description": "Read",
                                   "parameters": {"type": "object", "properties": {}}},
                          "execute": lambda: effects.append(1) or "evidence"}],
                  response_format={"type": "json_schema", "schema": SCHEMA, "fallback": "prompt"})
    if not structured:
        kwargs.pop("response_format")
    with pytest.raises(Exception, match=ERROR):
        runtime.exec("Read once then answer", **kwargs)
    state = states[-1]
    assert state.phase == "paused"
    assert state.transport_used == state.transport_limit
    assert len(calls) == 1 + 1 + state.transport_limit, "the tool round is never replayed"
    assert all(receipts == ["lookup-1"] for receipts in calls[1:])
    assert effects == [1]
    assert state.used == 0


def test_standalone_agent_session_retains_retry_behavior_without_enclosing_state():
    calls = []
    model = Runtime(call=lambda *a, **k: "unused", model="dummy").api_model

    async def stream(model, context, options=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(ERROR)
        msg = _response(model, [TextContent(text="done")], len(calls))
        yield EventStart(partial=msg)
        yield EventDone(reason="stop", message=msg)

    session = AgentSession(model=model, stream_fn=stream)
    try:
        assert current_recovery.get() is None
        asyncio.run(session.run("Answer"))
        assert session.last_assistant.content[0].text == "done"
        assert len(calls) == 2
        assert current_recovery.get() is None
    finally:
        session.close()


@pytest.mark.parametrize("failures", [1, 3])
def test_runtime_recovers_responses_reasoning_then_generation_error_without_tool_replay(failures):
    from openprogram.models.providers.shared.openai_responses import process_responses_stream
    from openprogram.models.providers.utils.event_stream import EventStream

    calls, effects, observed = [], [], []

    async def stream(model, context, options=None):
        receipts = [m.tool_call_id for m in context.messages if m.role == "toolResult"]
        calls.append(receipts)
        n = len(calls)
        if 2 <= n <= failures + 1:
            output = AssistantMessage(content=[], api=model.api, provider=model.provider,
                                      model=model.id, timestamp=n)
            events = EventStream()

            async def feed():
                yield {"type": "response.output_item.added", "output_index": 0,
                       "item": {"type": "reasoning", "id": "rs_1"}}
                yield {"type": "response.reasoning_summary_text.delta", "output_index": 0,
                       "delta": "Checking available source evidence"}
                yield {"type": "error", "code": None,
                       "message": "Internal error during token generation"}

            try:
                await process_responses_stream(feed(), output, events, model)
            except RuntimeError as exc:
                events.fail(exc)
            async for event in events:
                observed.append(event.type)
                yield event
            return
        content = ([TextContent(text='{"answer":7}')] if receipts else
                   [ToolCall(id=f"lookup-{n}", name="lookup", arguments={})])
        msg = _response(model, content, n)
        yield EventStart(partial=msg)
        yield EventDone(reason=msg.stop_reason, message=msg)

    runtime = Runtime(call=lambda *a, **k: "unused", model="dummy", max_retries=failures + 1)
    result = runtime.exec("Read once then answer", stream_fn=stream,
                          tools=[{"spec": {"name": "lookup", "description": "Read",
                                           "parameters": {"type": "object", "properties": {}}},
                                  "execute": lambda: effects.append(1) or "evidence"}],
                          response_format={"type": "json_schema", "schema": SCHEMA, "fallback": "prompt"})
    assert result == {"answer": 7}
    assert calls == [[]] + [["lookup-1"]] * (failures + 1)
    assert effects == [1]
    assert observed.count("thinking_delta") == failures
