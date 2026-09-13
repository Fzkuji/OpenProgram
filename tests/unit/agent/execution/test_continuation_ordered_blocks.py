"""Continuation ordered-card seeding uses full turn_display, not only the last decision."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from openprogram.agent.continuation import (
    AgentCheckpointV1,
    AgentContinuation,
    canonical_json_bytes,
    runtime_contract_snapshot,
)
from openprogram.agent.dispatcher.loop_runner import (
    _ordered_block_from_content,
    _seed_continuation_cards,
)
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.providers.types import (
    AssistantMessage,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
)


def _model():
    return Model(
        id="fake", name="fake", api="openai-completions", provider="openai",
        base_url="https://example.invalid/v1",
    )


async def _fixture_execute(_call_id, _args, _cancel, _update):
    return AgentToolResult(content=[TextContent(text="fixture")])


def _web_use_tool():
    return AgentTool(
        name="web_use", description="web_use",
        parameters={"type": "object"}, label="web_use",
        execute=_fixture_execute,
    )


def _request():
    request = TurnRequest(
        session_id="session", user_text="run", agent_id="main", source="test",
        user_msg_id="user-1", user_already_persisted=True,
    )
    request._execution_revision_id = "test-revision"
    return request


def _snapshot(request, tools=None):
    return runtime_contract_snapshot(
        model=_model(), system_prompt="",
        tools=list(tools if tools is not None else [_web_use_tool()]),
        request=request,
    )


def _display_cards():
    return [
        {"type": "text", "text": "Counter is 1"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-1",
            "input": '{"command": "observe"}', "result": "Counter: 1", "is_error": False,
        },
        {"type": "text", "text": "Counter is 2"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-2",
            "input": '{"command": "act"}', "result": "Counter: 2", "is_error": False,
        },
        {"type": "text", "text": "Counter is 3"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-3",
            "input": '{"command": "observe"}', "result": "Counter: 3", "is_error": False,
        },
    ]


def test_ordered_block_from_content_maps_text_thinking_and_tool():
    assert _ordered_block_from_content(TextContent(text="hello")) == {
        "type": "text", "text": "hello",
    }
    thinking = SimpleNamespace(type="thinking", thinking="plan")
    assert _ordered_block_from_content(thinking) == {
        "type": "thinking", "text": "plan",
    }
    tool = ToolCall(id="call-pre", name="web_use", arguments={"command": "observe"})
    mapped = _ordered_block_from_content(tool)
    assert mapped["type"] == "tool"
    assert mapped["tool_call_id"] == "call-pre"


def test_seed_uses_full_display_when_current_decision_is_only_the_last_round():
    last = AssistantMessage(
        content=[
            TextContent(text="Counter is 3"),
            ToolCall(id="call-3", name="web_use", arguments={"command": "observe"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    continuation = SimpleNamespace(
        display=tuple(_display_cards()),
        assistant_message=last,
        tool_results=(
            ToolResultMessage(
                tool_call_id="call-3", tool_name="web_use",
                content=[TextContent(text="Counter: 3")], timestamp=1,
            ),
        ),
    )
    ordered: list[dict] = []
    tool_calls: list[dict] = []
    text_parts: list[str] = []
    seen_tools = _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=tool_calls,
        final_text_parts=text_parts,
    )
    assert [block.get("tool_call_id") or block.get("text") for block in ordered] == [
        "Counter is 1", "call-1", "Counter is 2", "call-2", "Counter is 3", "call-3",
    ]
    assert ordered[1]["result"] == "Counter: 1"
    assert ordered[1]["is_error"] is False
    assert text_parts == ["Counter is 3"]
    assert seen_tools == {"call-1", "call-2", "call-3"}
    assert {row["tool_call_id"] for row in tool_calls} == {"call-1", "call-2", "call-3"}


def test_seed_falls_back_to_current_decision_when_display_is_absent():
    continuation = SimpleNamespace(
        display=(),
        assistant_message=SimpleNamespace(content=[
            TextContent(text="Counter is 4"),
            ToolCall(id="call-pre", name="web_use", arguments={"command": "observe"}),
        ]),
        tool_results=(
            ToolResultMessage(
                tool_call_id="call-pre", tool_name="web_use",
                content=[TextContent(text="Counter: 4")], timestamp=1,
            ),
            ToolResultMessage(
                tool_call_id="call-orphan", tool_name="web_use",
                content=[TextContent(text="listed")], timestamp=1,
            ),
        ),
    )
    ordered: list[dict] = []
    tool_calls: list[dict] = []
    text_parts: list[str] = []
    seen = _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=tool_calls,
        final_text_parts=text_parts,
    )
    assert text_parts == ["Counter is 4"]
    assert [block["type"] for block in ordered] == ["text", "tool", "tool"]
    assert ordered[1]["tool_call_id"] == "call-pre"
    assert ordered[2]["tool_call_id"] == "call-orphan"
    assert seen == {"call-pre", "call-orphan"}


def test_seed_merges_current_decision_when_display_is_a_prefix():
    last = AssistantMessage(
        content=[
            TextContent(text="saved-now"),
            ToolCall(id="call-3", name="web_use", arguments={"command": "observe"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    prefix = [
        {"type": "text", "text": "repeated"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-1",
            "input": "{}", "result": "ok", "is_error": False,
        },
    ]
    continuation = SimpleNamespace(
        display=tuple(prefix),
        assistant_message=last,
        tool_results=(
            ToolResultMessage(
                tool_call_id="call-3", tool_name="web_use",
                content=[TextContent(text="Counter: 3")], timestamp=1,
            ),
        ),
    )
    ordered: list[dict] = []
    seen = _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=[],
        final_text_parts=[],
    )
    labels = [block.get("tool_call_id") or block.get("text") for block in ordered]
    assert labels == ["repeated", "call-1", "saved-now", "call-3"]
    assert seen == {"call-1", "call-3"}


def test_seed_appends_only_unmatched_tail_when_display_ends_mid_decision():
    last = AssistantMessage(
        content=[
            TextContent(text="Current"),
            ToolCall(id="call-new", name="web_use", arguments={"command": "act"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    display = [
        {"type": "text", "text": "Old"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-old",
            "result": "ok", "is_error": False,
        },
        {"type": "text", "text": "Current"},
    ]
    continuation = SimpleNamespace(
        display=tuple(display),
        assistant_message=last,
        tool_results=(),
    )
    ordered: list[dict] = []
    _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=[],
        final_text_parts=[],
    )
    labels = [block.get("tool_call_id") or block.get("text") for block in ordered]
    assert labels == ["Old", "call-old", "Current", "call-new"]
    assert labels.count("Current") == 1


def test_seed_appends_remaining_tools_when_display_ends_on_first_tool_of_decision():
    last = AssistantMessage(
        content=[
            TextContent(text="Current"),
            ToolCall(id="call-a", name="web_use", arguments={"command": "observe"}),
            ToolCall(id="call-b", name="web_use", arguments={"command": "act"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    display = [
        {"type": "text", "text": "Old"},
        {"type": "text", "text": "Current"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-a",
            "result": "ok", "is_error": False,
        },
    ]
    continuation = SimpleNamespace(
        display=tuple(display),
        assistant_message=last,
        tool_results=(),
    )
    ordered: list[dict] = []
    _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=[],
        final_text_parts=[],
    )
    labels = [block.get("tool_call_id") or block.get("text") for block in ordered]
    assert labels == ["Old", "Current", "call-a", "call-b"]
    assert ordered[2]["result"] == "ok"


def _blob_checkpoint(state: AgentCheckpointV1):
    payload_raw = canonical_json_bytes(dict(state.payload))
    digest = hashlib.sha256(payload_raw).hexdigest()
    main = {
        "ref": f"execstate://sha256/{digest}",
        "sha256": digest,
        "byte_length": len(payload_raw),
        "media_type": "application/json",
        "schema_version": 1,
    }
    blobs = dict(state.blob_payloads)
    blobs[main["ref"]] = payload_raw

    class _Store:
        def get_state_blob(self, execution_id, ref):
            raw = blobs.get(ref)
            if raw is None:
                return None
            return {
                "ref": ref,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_length": len(raw),
                "media_type": "application/json",
                "schema_version": 1,
                "payload": raw,
            }

    return _Store(), SimpleNamespace(
        execution_id="exec-1", state_refs={"agent_checkpoint": main},
    )


def test_from_checkpoint_exposes_decoded_turn_display():
    request = _request()
    last = AssistantMessage(
        content=[
            TextContent(text="Counter is 3"),
            ToolCall(id="call-3", name="web_use", arguments={"command": "observe"}),
        ],
        api="openai-completions", provider="openai", model="fake",
        timestamp=1, stop_reason="toolUse",
    )
    last_result = ToolResultMessage(
        tool_call_id="call-3", tool_name="web_use",
        content=[TextContent(text="Counter: 3")], timestamp=1,
    )
    snapshot = _snapshot(request)
    state = AgentCheckpointV1.build(
        safe_point={
            "kind": "agent.tool.action.after", "step_id": "after_tool:p",
            "phase": "after_tool", "sentinel": "resume-from-checkpoint",
        },
        frontier=[{"step_id": "after_tool:p", "phase": "after_tool", "branch_id": "main"}],
        turn={
            "user_message_id": "user-1", "assistant_message_id": "user-1_reply",
            "base_history_head_id": "user-1",
        },
        assistant_message=last.model_dump(mode="json"),
        tool_results=[last_result.model_dump(mode="json")],
        resolved_snapshot=snapshot,
        provider_action_id="provider-action",
        tool_call_ids=["call-3"],
        next_tool_index=1,
        repeat_failures={},
        completed_actions=[
            {"action_id": "provider-action", "input_hash": "context-hash"},
            {"action_id": "tool-action-1", "input_hash": "tool-hash"},
        ],
        terminal_effect_receipts=[
            {
                "effect_id": "effect-provider", "frontier_step_id": "provider:p",
                "action_id": "provider-action", "outcome": "committed",
                "receipt": {"provider_request_id": "saved-request"},
            },
            {
                "effect_id": "effect-tool", "frontier_step_id": "after_tool:p",
                "action_id": "tool-action-1", "outcome": "committed",
                "receipt": {"tool_call_id": "call-3"},
            },
        ],
        turn_display=_display_cards(),
    )
    store, checkpoint = _blob_checkpoint(state)
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint, request=request,
    )
    assert [card.get("tool_call_id") for card in continuation.display if card.get("type") == "tool"] == [
        "call-1", "call-2", "call-3",
    ]
    assert continuation.assistant_message.content[0].text == "Counter is 3"
    assert continuation.display[1]["result"] == "Counter: 1"
