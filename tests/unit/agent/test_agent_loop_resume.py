"""Durable Agent loop continuation behavior."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openprogram.agent.agent_loop import agent_loop_resume
from openprogram.agent.continuation import AgentCheckpointV1, AgentContinuation
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.types import AgentContext, AgentLoopConfig, AgentTool, AgentToolResult
from openprogram.providers.types import (
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
    from openprogram.agent.continuation import runtime_contract_snapshot

    request = TurnRequest(
        session_id="session", user_text="run", agent_id="main", source="test",
        user_msg_id="user-1", user_already_persisted=True,
    )
    request._execution_revision_id = "test-revision"
    decision = decision or _assistant([
        ToolCall(id="tool-1", name="echo", arguments={"value": "saved"}),
    ])
    tool_call_ids = [
        item.id for item in decision.content if isinstance(item, ToolCall)
    ]
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
    completed_actions = [{"action_id": "provider-action", "input_hash": "context-hash"}]
    receipts = [{
        "effect_id": "effect-provider", "frontier_step_id": "provider:p",
        "action_id": "provider-action", "outcome": "committed",
        "receipt": {"provider_request_id": "saved-request"},
    }]
    if tool_results:
        completed_actions.append({"action_id": "tool-action-1", "input_hash": "tool-hash"})
        receipts.append({
            "effect_id": "effect-tool", "frontier_step_id": "after_tool:p",
            "action_id": "tool-action-1", "outcome": "committed",
            "receipt": {"tool_call_id": tool_results[-1].tool_call_id},
        })
    state = AgentCheckpointV1.build(
        safe_point={
            "kind": (
                "agent.provider.decision.after"
                if phase == "after_provider"
                else "agent.tool.action.after"
            ),
            "step_id": f"{phase}:p", "phase": phase,
            "sentinel": "resume-from-checkpoint",
        },
        frontier=[{"step_id": f"{phase}:p", "phase": phase, "branch_id": "main"}],
        turn={
            "user_message_id": "user-1", "assistant_message_id": "user-1_reply",
            "base_history_head_id": "user-1",
        },
        assistant_message=decision.model_dump(mode="json"),
        tool_results=[item.model_dump(mode="json") for item in tool_results],
        resolved_snapshot=resolved_snapshot,
        provider_action_id="provider-action", tool_call_ids=tool_call_ids,
        next_tool_index=next_tool_index, repeat_failures={},
        completed_actions=completed_actions,
        terminal_effect_receipts=receipts,
    )
    return AgentContinuation(
        request=request, checkpoint=SimpleNamespace(), state=state,
        assistant_message=decision, tool_results=tool_results,
        resolved_snapshot=resolved_snapshot,
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
