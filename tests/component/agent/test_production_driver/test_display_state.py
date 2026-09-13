"""driver display state tests."""
from __future__ import annotations
from ._support import (
    CommandStatus,
    ExecutionStatus,
    NATIVE_DISPLAY_ROUNDS,
    _committed_tool_ids,
    _native_observe_result,
    _prepare_long_turn,
    _provider_tool_decision,
    _public_display_checkpoint,
    asyncio,
    json,
    threading,
)


def test_native_sized_web_use_display_pause_continue(tmp_path):
    from openprogram.agent.continuation import (
        MAX_AGENT_DELTA_BYTES,
        AgentCheckpointV1,
        AgentContinuation,
    )
    from openprogram.execution.checkpoints import ExecutionCheckpointStore
    from openprogram.execution.public import execution_snapshot

    sample = [
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": f"call-{index}",
            "result": _native_observe_result(index),
        }
        for index in range(NATIVE_DISPLAY_ROUNDS)
    ]
    assert len(json.dumps(sample, ensure_ascii=False).encode("utf-8")) > MAX_AGENT_DELTA_BYTES

    store, _attempts, control, driver, request, snapshot, execution, active, hook = (
        _prepare_long_turn(tmp_path, "exec-native-display")
    )
    for index in range(NATIVE_DISPLAY_ROUNDS):
        _provider_tool_decision(
            hook, snapshot, index, result_text=_native_observe_result(index),
        )
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["native-pause-1"]},
        "supports_idempotency_key": True,
    }) is False
    asyncio.run(control.request_pause(
        command_id="pause-native-display-1",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "browser-resource"},
    ))
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": [{"type": "text", "text": "paused-native-1"}],
            "api": "fake", "provider": "fake", "model": "fake", "timestamp": 1,
        },
        "provider_request_id": "native-pause-1",
        "usage": {},
        "tool_call_ids": [],
        "next_tool_index": 0,
    }) is True
    paused = store.get_execution(execution.execution_id)
    assert paused is not None and paused.status is ExecutionStatus.PAUSED
    assert paused.checkpoint_head_id is not None
    assert store.get_command("pause-native-display-1").status is CommandStatus.APPLIED
    assert execution_snapshot(paused, store=store).can_continue is True
    state = AgentCheckpointV1.load(
        store, ExecutionCheckpointStore(store).get(paused.checkpoint_head_id),
    )
    display = state.read_json_ref(store, execution.execution_id, state.payload["turn_display_ref"])
    assert display[0]["tool_call_id"] == "call-0"
    assert f"Counter: 0" in display[0]["result"]
    assert display[NATIVE_DISPLAY_ROUNDS - 1]["tool_call_id"] == f"call-{NATIVE_DISPLAY_ROUNDS - 1}"
    first_tools = _committed_tool_ids(store, execution.execution_id)
    assert len(first_tools) == NATIVE_DISPLAY_ROUNDS

    captured = {}

    async def activate(attempt, activation):
        captured["attempt"] = attempt
        captured["activation"] = activation

    continued = asyncio.run(control.request_continue(
        command_id="continue-native-display-1",
        execution_id=execution.execution_id,
        expected_version=paused.status_version,
        actor={"surface": "test"},
        activator=activate,
    ))
    assert continued.command.status is CommandStatus.APPLIED
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=captured["activation"].checkpoint, request=request,
    )
    hook2 = driver._safe_point_hook(
        captured["attempt"], request, threading.Event(), continuation=continuation,
    )
    second_start = NATIVE_DISPLAY_ROUNDS
    second_end = NATIVE_DISPLAY_ROUNDS + 20
    for index in range(second_start, second_end):
        _provider_tool_decision(
            hook2, snapshot, index, result_text=_native_observe_result(index),
        )
    assert hook2("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["native-pause-2"]},
        "supports_idempotency_key": True,
    }) is False
    asyncio.run(control.request_pause(
        command_id="pause-native-display-2",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "test"},
    ))
    assert hook2("provider.after", {
        "message": {
            "role": "assistant", "content": [{"type": "text", "text": "paused-native-2"}],
            "api": "fake", "provider": "fake", "model": "fake", "timestamp": 1,
        },
        "provider_request_id": "native-pause-2",
        "usage": {},
        "tool_call_ids": [],
        "next_tool_index": 0,
    }) is True
    paused_again = store.get_execution(execution.execution_id)
    assert paused_again is not None and paused_again.status is ExecutionStatus.PAUSED
    second_state = AgentCheckpointV1.load(
        store, ExecutionCheckpointStore(store).get(paused_again.checkpoint_head_id),
    )
    second_display = second_state.read_json_ref(
        store, execution.execution_id, second_state.payload["turn_display_ref"],
    )
    assert any(block.get("tool_call_id") == "call-0" and "Counter: 0" in block.get("result", "") for block in second_display)
    assert any(
        block.get("tool_call_id") == f"call-{second_start}"
        and f"Counter: {second_start}" in block.get("result", "")
        for block in second_display
    )
    second_tools = _committed_tool_ids(store, execution.execution_id)
    assert second_tools[:NATIVE_DISPLAY_ROUNDS] == first_tools
    assert len(set(second_tools)) == len(second_tools)
    resumed = asyncio.run(control.request_continue(
        command_id="continue-native-display-2",
        execution_id=execution.execution_id,
        expected_version=paused_again.status_version,
        actor={"surface": "test"},
        activator=activate,
    ))
    assert resumed.command.status is CommandStatus.APPLIED
    assert _committed_tool_ids(store, execution.execution_id) == second_tools



def test_display_ref_overflow_keeps_fitting_pages_in_temp_store(tmp_path):
    from openprogram.agent.continuation import (
        MAX_AGENT_STATE_REFS,
        decode_turn_display,
    )

    display = [
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": f"call-{index}",
            "result": f"{index}:" + ("P" * (600 * 1024)),
        }
        for index in range(30)
    ]
    store, execution, state = _public_display_checkpoint(
        tmp_path, turn_display=display, execution_id="exec-display-overflow",
    )
    state.validate()
    assert len(state.payload["turn_display_refs"]) == 29
    assert len(state.payload["state_refs"]) == MAX_AGENT_STATE_REFS
    decoded = decode_turn_display(
        state, store=store, execution_id=execution.execution_id,
    )
    assert decoded[0]["tool_call_id"] == "call-0"
    assert decoded[-1]["tool_call_id"] == "call-28"
    assert not any(block.get("tool_call_id") == "call-29" for block in decoded)



def test_shared_pending_display_digest_survives_ref_budget_in_temp_store(tmp_path):
    from openprogram.agent.continuation import (
        MAX_AGENT_STATE_REFS,
        decode_turn_display,
    )

    display = [{"type": "text", "text": "kept-card"}]
    store, execution, state = _public_display_checkpoint(
        tmp_path,
        turn_display=display,
        receipt_count=29,
        pending_messages=[{
            "message_id": "pending-1",
            "sequence": 0,
            "input_hash": "pending-hash",
            "status": "pending",
            "content": display,
        }],
        execution_id="exec-display-alias",
    )
    state.validate()
    payload = state.payload
    assert len(payload["state_refs"]) == MAX_AGENT_STATE_REFS
    pending_ref = payload["pending_messages"][0]["content_ref"]
    blob = store.get_state_blob(execution.execution_id, pending_ref["ref"])
    assert blob is not None
    assert json.loads(blob["payload"].decode("utf-8")) == display
    assert "turn_display_ref" not in payload
    assert "turn_display" not in payload["state_refs"]
    decoded = decode_turn_display(
        state, store=store, execution_id=execution.execution_id,
    )
    assert not any(block.get("text") == "kept-card" for block in decoded)



def test_paged_turn_display_decode_uses_temp_store(tmp_path):
    from openprogram.agent.continuation import (
        MAX_AGENT_STATE_BLOB_BYTES,
        decode_turn_display,
    )

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
    store, execution, state = _public_display_checkpoint(
        tmp_path, turn_display=display, execution_id="exec-display-pages",
    )
    state.validate()
    page_refs = state.payload["turn_display_refs"]
    assert len(page_refs) >= 2
    assert all(
        state.payload["state_refs"][f"turn_display.{index}"] == descriptor
        for index, descriptor in enumerate(page_refs)
    )
    assert "turn_display_result.0" in state.payload["state_refs"]
    decoded = decode_turn_display(
        state, store=store, execution_id=execution.execution_id,
    )
    assert [block["tool_call_id"] for block in decoded] == [
        "call-huge", "call-small", "call-a", "call-b",
    ]
    assert decoded[0]["result"] == huge
    assert "result_ref" not in decoded[0]
    assert decoded[1]["result"] == "small"
    assert decoded[2]["result"].startswith("A:")
    assert decoded[3]["result"].startswith("B:")

