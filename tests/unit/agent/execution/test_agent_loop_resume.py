"""Durable Agent loop continuation behavior."""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from openprogram.agent.agent_loop import agent_loop_resume
from openprogram.agent.continuation import (
    AgentCheckpointError,
    AgentCheckpointV1,
    AgentContinuation,
    decode_turn_display,
)
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


def _blob_store(state: AgentCheckpointV1):
    blobs = dict(state.blob_payloads)

    class _Store:
        def get_state_blob(self, execution_id, ref):
            raw = blobs.get(ref)
            if raw is None:
                return None
            digest = hashlib.sha256(raw).hexdigest()
            return {
                "ref": ref,
                "sha256": digest,
                "byte_length": len(raw),
                "media_type": "application/json",
                "schema_version": 1,
                "payload": raw,
            }

    return _Store()


def _decode_display(state: AgentCheckpointV1) -> list[dict]:
    return decode_turn_display(state, store=_blob_store(state), execution_id="exec-display")


def _continuation(
    *,
    phase: str = "after_provider",
    decision: AssistantMessage | None = None,
    tool_results: tuple[ToolResultMessage, ...] = (),
    next_tool_index: int = 0,
    turn_display: list[dict] | None = None,
    pending_messages: list[dict] | None = None,
    receipt_count: int = 1,
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
    assistant_dump = decision.model_dump(mode="json")
    while len(receipts) < receipt_count:
        index = len(receipts)
        action_id = f"history-action-{index}"
        completed_actions.append({
            "action_id": action_id,
            "input_hash": f"history-hash-{index}",
            "result": assistant_dump,
        })
        receipts.append({
            "effect_id": f"effect-history-{index}",
            "frontier_step_id": f"after:{index}",
            "action_id": action_id,
            "outcome": "committed",
            "receipt": {"n": index},
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
        pending_messages=pending_messages,
        turn_display=turn_display,
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


def test_agent_checkpoint_keeps_the_terminal_receipt_cap():
    from openprogram.agent.continuation import (
        MAX_AGENT_TERMINAL_EFFECT_RECEIPTS,
        AgentCheckpointError,
    )

    receipts = [
        {
            "effect_id": f"effect-{index}",
            "frontier_step_id": f"after:{index}",
            "action_id": f"action-{index}",
            "outcome": "committed",
            "receipt": {"n": index},
        }
        for index in range(MAX_AGENT_TERMINAL_EFFECT_RECEIPTS + 1)
    ]
    with pytest.raises(AgentCheckpointError, match="too many terminal effect receipts"):
        AgentCheckpointV1.build(
            safe_point={
                "kind": "agent.provider.decision.after",
                "step_id": "after_provider:p",
                "phase": "after_provider",
                "sentinel": "resume-from-checkpoint",
            },
            frontier=[{"step_id": "after_provider:p", "phase": "after_provider", "branch_id": "main"}],
            turn={
                "user_message_id": "user-1",
                "assistant_message_id": "user-1_reply",
                "base_history_head_id": "user-1",
            },
            assistant_message=_assistant([TextContent(text="x")]).model_dump(mode="json"),
            tool_results=[],
            resolved_snapshot={"model": {"id": "fake"}, "system_prompt": "", "tools": []},
            provider_action_id="action-0",
            tool_call_ids=[],
            next_tool_index=0,
            repeat_failures={},
            completed_actions=[
                {"action_id": f"action-{index}", "input_hash": "h"}
                for index in range(len(receipts))
            ],
            terminal_effect_receipts=receipts,
        )


def test_agent_checkpoint_accepts_turn_display_over_delta_cap():
    from openprogram.agent.continuation import (
        MAX_AGENT_DELTA_BYTES,
        MAX_AGENT_STATE_BLOB_BYTES,
    )

    display = [
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": f"call-{index}",
            "result": json.dumps({
                "frame_id": f"frame_{index}_a02ffb17",
                "url": "http://127.0.0.1:62147/page/1?acceptance=release",
                "aria_snapshot": ("- button: Test note\n" * 8) + ("pad-%s-" % index) + ("A" * 1400),
                "text": f"Counter: {index}",
            }, ensure_ascii=False),
        }
        for index in range(45)
    ]
    encoded = json.dumps(display, ensure_ascii=False).encode("utf-8")
    assert len(encoded) > MAX_AGENT_DELTA_BYTES
    assert len(encoded) < MAX_AGENT_STATE_BLOB_BYTES
    continuation = _continuation(turn_display=display)
    payload = continuation.state.payload
    assert "turn_display_ref" in payload
    blob = continuation.state.blob_payloads[payload["turn_display_ref"]["ref"]]
    assert len(blob) > MAX_AGENT_DELTA_BYTES
    assert json.loads(blob.decode("utf-8"))[0]["tool_call_id"] == "call-0"
    assert json.loads(blob.decode("utf-8"))[-1]["tool_call_id"] == "call-44"
    continuation.state.validate()


def test_agent_checkpoint_turn_display_ref_is_owned():
    import json

    display = [
        {"type": "text", "text": "Earlier progress."},
        {
            "type": "tool", "tool": "second", "tool_call_id": "call-finished",
            "result": "second:ok",
        },
    ]
    continuation = _continuation(turn_display=display)
    payload = continuation.state.payload
    assert "turn_display_ref" in payload
    assert "turn_display" in payload["state_refs"]
    continuation.state.validate()
    raw = continuation.state.blob_payloads[payload["turn_display_ref"]["ref"]]
    assert json.loads(raw.decode("utf-8")) == display


def test_agent_checkpoint_legacy_payload_omits_turn_display_ref():
    continuation = _continuation()
    payload = continuation.state.payload
    assert "turn_display_ref" not in payload
    assert "turn_display" not in payload["state_refs"]
    continuation.state.validate()


def test_agent_checkpoint_rejects_a_foreign_turn_display_ref():
    continuation = _continuation(turn_display=[{"type": "text", "text": "kept"}])
    payload = dict(continuation.state.payload)
    payload["turn_display_ref"] = payload["assistant_message_delta_ref"]
    with pytest.raises(AgentCheckpointError, match="turn display ref"):
        AgentCheckpointV1(
            payload=payload,
            blob_payloads=continuation.state.blob_payloads,
        ).validate()


def _paged_display_cards(count: int, *, size: int = 600 * 1024) -> list[dict]:
    return [
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": f"call-{index}",
            "result": f"{index}:" + ("P" * size),
        }
        for index in range(count)
    ]


def test_agent_checkpoint_keeps_fitting_display_pages_when_ref_budget_overflows():
    from openprogram.agent.continuation import MAX_AGENT_STATE_REFS

    continuation = _continuation(turn_display=_paged_display_cards(30))
    payload = continuation.state.payload
    continuation.state.validate()
    page_refs = payload["turn_display_refs"]
    assert len(page_refs) == 29
    assert len(payload["state_refs"]) == MAX_AGENT_STATE_REFS
    assert "turn_display.0" in payload["state_refs"]
    assert "turn_display.28" in payload["state_refs"]
    assert "turn_display.29" not in payload["state_refs"]
    decoded = _decode_display(continuation.state)
    assert decoded[0]["tool_call_id"] == "call-0"
    assert decoded[-1]["tool_call_id"] == "call-28"
    assert all(block.get("tool_call_id") == f"call-{index}" for index, block in enumerate(decoded))
    assert not any(block.get("tool_call_id") == "call-29" for block in decoded)


def test_agent_checkpoint_keeps_all_display_pages_that_already_fit_the_ref_cap():
    from openprogram.agent.continuation import MAX_AGENT_STATE_REFS

    continuation = _continuation(turn_display=_paged_display_cards(29))
    payload = continuation.state.payload
    continuation.state.validate()
    assert len(payload["turn_display_refs"]) == 29
    assert len(payload["state_refs"]) == MAX_AGENT_STATE_REFS
    decoded = _decode_display(continuation.state)
    assert [block["tool_call_id"] for block in decoded] == [f"call-{index}" for index in range(29)]


def test_agent_checkpoint_keeps_display_pages_that_fit_remaining_ref_budget():
    display = _paged_display_cards(3)
    continuation = _continuation(turn_display=display, receipt_count=28)
    payload = continuation.state.payload
    continuation.state.validate()
    assert len(payload["turn_display_refs"]) == 2
    assert "turn_display.0" in payload["state_refs"]
    assert "turn_display.1" in payload["state_refs"]
    assert "turn_display.2" not in payload["state_refs"]
    decoded = _decode_display(continuation.state)
    assert [block["tool_call_id"] for block in decoded] == ["call-0", "call-1"]
    assert not any(block.get("tool_call_id") == "call-2" for block in decoded)


def test_agent_checkpoint_display_drop_preserves_aliased_pending_blob():
    from openprogram.agent.continuation import MAX_AGENT_STATE_REFS

    display = [{"type": "text", "text": "kept-card"}]
    continuation = _continuation(
        turn_display=display,
        receipt_count=29,
        pending_messages=[{
            "message_id": "pending-1",
            "sequence": 0,
            "input_hash": "pending-hash",
            "status": "pending",
            "content": display,
        }],
    )
    payload = continuation.state.payload
    continuation.state.validate()
    assert len(payload["state_refs"]) == MAX_AGENT_STATE_REFS
    pending_ref = payload["pending_messages"][0]["content_ref"]
    assert payload["state_refs"]["pending_message.0"] == pending_ref
    assert pending_ref["ref"] in continuation.state.blob_payloads
    assert json.loads(
        continuation.state.blob_payloads[pending_ref["ref"]].decode("utf-8")
    ) == display
    assert "turn_display_ref" not in payload
    assert "turn_display_refs" not in payload
    assert "turn_display" not in payload["state_refs"]


def test_agent_checkpoint_paged_turn_display_round_trips_in_order():
    from openprogram.agent.continuation import MAX_AGENT_STATE_BLOB_BYTES

    huge = "H" * (MAX_AGENT_STATE_BLOB_BYTES - 32)
    display = [
        {"type": "tool", "tool": "web_use", "tool_call_id": "call-huge", "result": huge},
        {"type": "tool", "tool": "web_use", "tool_call_id": "call-small", "result": "small"},
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": "call-a",
            "result": "A:" + ("A" * (600 * 1024)),
        },
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": "call-b",
            "result": "B:" + ("B" * (600 * 1024)),
        },
    ]
    continuation = _continuation(turn_display=display)
    payload = continuation.state.payload
    continuation.state.validate()
    page_refs = payload["turn_display_refs"]
    assert len(page_refs) >= 2
    assert all(
        payload["state_refs"][f"turn_display.{index}"] == descriptor
        for index, descriptor in enumerate(page_refs)
    )
    assert "turn_display_result.0" in payload["state_refs"]
    decoded = _decode_display(continuation.state)
    assert [block["tool_call_id"] for block in decoded] == [
        "call-huge", "call-small", "call-a", "call-b",
    ]
    assert decoded[0]["result"] == huge
    assert "result_ref" not in decoded[0]
    assert decoded[1]["result"] == "small"
    assert decoded[2]["result"].startswith("A:")
    assert decoded[3]["result"].startswith("B:")


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
    from openprogram.agentic_programming.function import CancelledError
    from openprogram.providers.utils.errors import ExecInterrupt

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
                assert await asyncio.wait_for(stream.result(), timeout=1) == []
            assert calls == ['tool']
        finally:
            await stream.cancel_producer()

    asyncio.run(run(CancelledError, True))
    asyncio.run(run(ExecInterrupt, True))
    asyncio.run(run(ExecInterrupt, False))
