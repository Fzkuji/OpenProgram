"""Durable Agent loop continuation behavior."""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from openprogram.runtime.agent.loop import agent_loop_resume
from tests.support.agent_journal import journal_continuation
from openprogram.runtime.conversation.checkpoint import AgentContinuation
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
)


def _model() -> Model:
    return Model(
        id="fake", name="fake", api="openai-completions", provider="openai",
        base_url="https://example.invalid/v1",
    )


def _assistant(content) -> AssistantMessage:
    return AssistantMessage(
        content=content, api="openai-completions", provider="openai",
        model="fake", stop_reason="toolUse" if any(
            isinstance(item, ToolCall) for item in content
        ) else "stop", timestamp=1,
    )


def _continuation(
    *,
    phase: str = "after_provider",
    decision: AssistantMessage | None = None,
    tool_results: tuple[ToolResultMessage, ...] = (),
    next_tool_index: int = 0,
) -> AgentContinuation:
    from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot

    request = TurnRequest(
        session_id="session", user_text="run", agent_id="main", source="test",
        user_msg_id="user-1", user_already_persisted=True,
    )
    request._execution_revision_id = "test-revision"
    decision = decision or _assistant([
        ToolCall(id="tool-1", name="echo", arguments={"value": "saved"}),
    ])
    async def _fixture_execute(_call_id, _args, _cancel, _update):
        return AgentToolResult(content=[TextContent(text="fixture")])

    fixture_tools = [
        AgentTool(
            name=item.name, description=item.name,
            parameters={"type": "object"}, label=item.name,
            execute=_fixture_execute,
        )
        for item in decision.content if isinstance(item, ToolCall)
    ]
    resolved_snapshot = runtime_contract_snapshot(
        model=_model(), system_prompt="", tools=fixture_tools,
        request=request,
    )
    return journal_continuation(
        request, decision=decision, snapshot=resolved_snapshot, tool_results=tuple(tool_results),
        execution_id="exec-display",
    )


def test_after_provider_resume_executes_saved_tool_before_one_new_provider_call():
    continuation = _continuation()
    calls = {"provider": 0, "tool": 0}

    async def execute(_call_id, args, _cancel, _update):
        calls["tool"] += 1
        assert args == {"value": "saved"}
        return AgentToolResult(content=[TextContent(text="saved-result")])

    tool = AgentTool(
        name="echo", description="echo", parameters={"type": "object"},
        label="echo", execute=execute,
    )

    def stream_fn(_model, _context, _options):
        calls["provider"] += 1

        async def stream():
            yield EventDone(reason="stop", message=_assistant([TextContent(text="done")]))

        return stream()

    config = AgentLoopConfig(
        model=_model(), convert_to_llm=lambda messages: messages,
    )
    async def run():
        stream = agent_loop_resume(
            continuation, AgentContext(messages=[], tools=[tool]), config,
            stream_fn=stream_fn,
        )
        return await stream.result()

    assert asyncio.run(run())
    assert calls == {"provider": 1, "tool": 1}


def test_after_tool_resume_executes_only_the_unfinished_tool_suffix():
    decision = _assistant([
        ToolCall(id="tool-1", name="first", arguments={"value": "one"}),
        ToolCall(id="tool-2", name="second", arguments={"value": "two"}),
    ])
    first_result = ToolResultMessage(
        tool_call_id="tool-1", tool_name="first",
        content=[TextContent(text="first-result")], timestamp=1,
    )
    continuation = _continuation(
        phase="after_tool", decision=decision, tool_results=(first_result,),
        next_tool_index=1,
    )
    calls = {"provider": 0, "first": 0, "second": 0}

    def tool(name: str) -> AgentTool:
        async def execute(_call_id, _args, _cancel, _update):
            calls[name] += 1
            return AgentToolResult(content=[TextContent(text=f"{name}-result")])

        return AgentTool(
            name=name, description=name, parameters={"type": "object"},
            label=name, execute=execute,
        )

    def stream_fn(_model, _context, _options):
        calls["provider"] += 1

        async def stream():
            yield EventDone(reason="stop", message=_assistant([TextContent(text="done")]))

        return stream()

    config = AgentLoopConfig(model=_model(), convert_to_llm=lambda messages: messages)

    async def run():
        stream = agent_loop_resume(
            continuation, AgentContext(messages=[], tools=[tool("first"), tool("second")]),
            config, stream_fn=stream_fn,
        )
        return await stream.result()

    assert asyncio.run(run())
    assert calls == {"provider": 1, "first": 0, "second": 1}


def test_resume_after_an_approval_wait_keeps_earlier_results_in_the_provider_context():
    """A wait before the second call is an after_provider point that still
    carries the first call's result; the next provider call must see both."""
    decision = _assistant([
        ToolCall(id="tool-1", name="first", arguments={"value": "one"}),
        ToolCall(id="tool-2", name="second", arguments={"value": "two"}),
    ])
    first_result = ToolResultMessage(
        tool_call_id="tool-1", tool_name="first",
        content=[TextContent(text="first-result")], timestamp=1,
    )
    continuation = _continuation(
        phase="after_provider", decision=decision, tool_results=(first_result,),
        next_tool_index=1,
    )
    calls = {"first": 0, "second": 0}
    seen: list[list] = []

    def tool(name: str) -> AgentTool:
        async def execute(_call_id, _args, _cancel, _update):
            calls[name] += 1
            return AgentToolResult(content=[TextContent(text=f"{name}-result")])

        return AgentTool(
            name=name, description=name, parameters={"type": "object"},
            label=name, execute=execute,
        )

    def stream_fn(_model, context, _options):
        seen.append([
            (message.tool_call_id, message.content[0].text)
            for message in context.messages
            if getattr(message, "role", None) == "toolResult"
        ])

        async def stream():
            yield EventDone(reason="stop", message=_assistant([TextContent(text="done")]))

        return stream()

    config = AgentLoopConfig(model=_model(), convert_to_llm=lambda messages: messages)

    async def run():
        stream = agent_loop_resume(
            continuation, AgentContext(messages=[], tools=[tool("first"), tool("second")]),
            config, stream_fn=stream_fn,
        )
        return await stream.result()

    assert asyncio.run(run())
    assert calls == {"first": 0, "second": 1}
    assert seen == [[("tool-1", "first-result"), ("tool-2", "second-result")]]


def _paged_display_cards(count: int, *, size: int = 600 * 1024) -> list[dict]:
    return [
        {
            "type": "tool",
            "tool": "page_tool",
            "tool_call_id": f"call-{index}",
            "result": f"{index}:" + ("P" * size),
        }
        for index in range(count)
    ]


def test_terminal_after_provider_resume_never_replays_the_provider():
    continuation = _continuation(
        decision=_assistant([TextContent(text="saved final answer")]),
    )

    def stream_fn(*_args):
        raise AssertionError("terminal saved answer must not call provider")

    config = AgentLoopConfig(model=_model(), convert_to_llm=lambda messages: messages)

    async def run():
        stream = agent_loop_resume(
            continuation, AgentContext(messages=[], tools=[]), config,
            stream_fn=stream_fn,
        )
        return await stream.result()

    result = asyncio.run(run())
    assert result == [continuation.assistant_message]


def test_resumed_tool_interrupt_closes_stream_without_provider_replay():
    import pytest
    from openprogram.host.cancellation import CancelledError
    from openprogram.models.providers.utils.errors import ExecInterrupt

    async def run(error_type, cancelled):
        event = asyncio.Event()
        calls = []

        async def execute(*_args):
            calls.append('tool')
            if cancelled:
                event.set()
            raise error_type('cancelled')

        async def provider(*_args, **_kwargs):
            raise AssertionError('cancelled continuation requested another provider decision')
            yield  # pragma: no cover

        tool = AgentTool(
            name='echo', label='echo', description='echo',
            parameters={'type': 'object'}, execute=execute,
        )
        stream = agent_loop_resume(
            _continuation(), AgentContext(system_prompt='', messages=[], tools=[tool]),
            AgentLoopConfig(model=_model(), convert_to_llm=lambda messages: messages), event, provider,
        )
        try:
            if error_type is ExecInterrupt and not cancelled:
                with pytest.raises(ExecInterrupt):
                    await asyncio.wait_for(stream.result(), timeout=1)
            else:
                assert await asyncio.wait_for(stream.result(), timeout=30) == []
            assert calls == ['tool']
        finally:
            await stream.cancel_producer()

    asyncio.run(run(CancelledError, True))
    asyncio.run(run(ExecInterrupt, True))
    asyncio.run(run(ExecInterrupt, False))


@pytest.mark.parametrize("boundary", ["provider.before", "tool.before", "tool.started"])
def test_resume_handoff_before_dispatch_does_not_run_operation(boundary):
    continuation = _continuation()
    calls = {"provider": 0, "tool": 0}

    async def execute(*_args):
        calls["tool"] += 1
        return AgentToolResult(content=[TextContent(text="ok")])

    async def hook(kind, _payload):
        return kind == boundary

    def stream_fn(*_args):
        calls["provider"] += 1
        pytest.fail("provider must not start after handoff")

    tool = AgentTool(name="echo", description="echo", parameters={"type": "object"},
                     label="echo", execute=execute)
    config = AgentLoopConfig(model=_model(), convert_to_llm=lambda messages: messages,
                             safe_point_hook=hook)

    async def run():
        stream = agent_loop_resume(continuation, AgentContext(messages=[], tools=[tool]),
                                   config, stream_fn=stream_fn)
        events = [event async for event in stream]
        assert events[-1].type == "agent_end"
        assert not any(getattr(event, "is_error", False) for event in events)

    asyncio.run(run())
    assert calls == {"provider": 0, "tool": int(boundary == "provider.before")}
