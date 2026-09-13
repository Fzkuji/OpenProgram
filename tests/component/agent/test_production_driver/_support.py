from __future__ import annotations


import asyncio


import json


import sqlite3


import sys


import threading


import time


from types import SimpleNamespace


import pytest


from openprogram.execution import AttemptStore, CapabilitySet, ExecutionStore


from openprogram.execution._schema import SCHEMA_VERSION


from openprogram.execution.model import CommandKind, CommandStatus, ExecutionStatus


def _admitted(tmp_path, *, execution_id="exec-agent-1"):
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    attempts = AttemptStore(store)
    revision = store.create_revision(
        revision_id="revision-agent-1", manifest={"entrypoint": "agent"}
    )
    execution = store.admit_execution(
        execution_id=execution_id,
        run_id="run-agent-1",
        session_id="session-agent-1",
        revision_id=revision.revision_id,
        input_ref=f"input:{execution_id}",
        input_hash="input-hash-1",
        entrypoint="openprogram.agent.dispatcher:process_user_turn",
        trusted_actor={"subject": "user-1", "session_id": "session-agent-1"},
        config_snapshot_ref="config:agent-1",
        capabilities=CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            safe_point_kinds=(
                "agent.provider.decision.after",
                "agent.tool.action.after",
                "agent.wait.before_tool",
            ),
            state_schema_version=1,
        ),
        agent_turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "durable agent turn",
                "agent_id": "default",
                "source": "web",
                "permission_mode": "ask",
            },
        },
    )
    return store, execution


def _hold_canonical_agent_process(db_path, ready):
    from openprogram.agent.production_driver import AgentProductionDriver, CanonicalAgentEntry

    store = ExecutionStore(db_path)
    def work(*, request, cancel_event):
        ready.send(admission.execution_id)
        threading.Event().wait(20)
        return SimpleNamespace(failed=False)
    driver = AgentProductionDriver(store, turn_runner=work)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="process-owner", turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    async def run():
        active = await entry.activate(admission)
        await driver._handles[(admission.execution_id, active.attempt_id, active.generation)].done
    asyncio.run(run())


LONG_TURN_DECISIONS = 70


def _long_turn_snapshot(request):
    from openprogram.agent.continuation import runtime_contract_snapshot
    from openprogram.providers.types import Model

    return runtime_contract_snapshot(
        model=Model(
            id="fake", name="fake", api="openai-completions", provider="openai",
            base_url="https://example.invalid/v1",
        ),
        system_prompt="system",
        tools=[],
        request=request,
    )


def _native_observe_result(index) -> str:
    return json.dumps(
        {
            "frame_id": f"frame_{index}_a02ffb17",
            "url": "http://127.0.0.1:62147/page/1?acceptance=release",
            "origin": "http://127.0.0.1:62147",
            "title": "Resource test 1",
            "text": f"Counter: {index}\n" + ("visible-text " * 20),
            "aria_snapshot": "- document\n" + ("- button: Test note\n" * 12) + f"- counter: {index}\n" + ("x" * 900),
            "elements": [{"role": "button", "name": "Test note", "index": index}],
            "backend": "open_claude_chrome",
        },
        ensure_ascii=False,
    )


def _provider_tool_decision(hook, snapshot, index, *, result_text=None):
    tool_call_id = f"call-{index}"
    assistant = {
        "role": "assistant",
        "content": [{
            "type": "toolCall",
            "id": tool_call_id,
            "name": "web_use",
            "arguments": {"n": index},
        }],
        "api": "fake",
        "provider": "fake",
        "model": "fake",
        "timestamp": 1,
    }
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": [f"round-{index}"]},
        "supports_idempotency_key": True,
    }) is False
    assert hook("provider.after", {
        "message": assistant,
        "provider_request_id": f"request-{index}",
        "usage": {},
        "tool_call_ids": [tool_call_id],
        "next_tool_index": 0,
    }) is False
    assert hook("tool.before", {
        "tool_call_id": tool_call_id,
        "arguments": {"n": index},
    }) is False
    assert hook("tool.after", {
        "tool_call_id": tool_call_id,
        "is_error": False,
        "result": {
            "role": "toolResult",
            "tool_call_id": tool_call_id,
            "content": [{"type": "text", "text": result_text if result_text is not None else f"clicked-{index}"}],
        },
        "tool_call_ids": [tool_call_id],
        "next_tool_index": 1,
    }) is False
    return tool_call_id


def _committed_tool_ids(store, execution_id):
    from openprogram.execution.effects import EffectStatus

    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT action_id, status, metadata_json FROM effects "
            "WHERE execution_id = ? ORDER BY created_at, effect_id",
            (execution_id,),
        ).fetchall()
    ids = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        if metadata.get("kind") != "tool.before":
            continue
        if row["status"] != EffectStatus.COMMITTED.value:
            continue
        ids.append(row["action_id"])
    return ids


def _prepare_long_turn(tmp_path, execution_id):
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id=execution_id)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id=f"owner-{execution_id}",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    control = RuntimeControlService(store, attempts, DriverRegistry())
    request = TurnRequest(
        session_id=running.session_id,
        user_text="durable agent turn",
        agent_id="default",
        source="component",
        user_msg_id="user-anchor",
    )
    request._execution_revision_id = running.revision_id
    driver = AgentProductionDriver(store, control_service=control)
    hook = driver._safe_point_hook(active, request, threading.Event())
    snapshot = _long_turn_snapshot(request)
    return store, attempts, control, driver, request, snapshot, execution, active, hook


NATIVE_DISPLAY_ROUNDS = 50


def _public_display_checkpoint(
    tmp_path,
    *,
    turn_display,
    receipt_count=1,
    pending_messages=None,
    execution_id="exec-display-cap",
):
    from openprogram.agent.continuation import AgentCheckpointV1
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.providers.types import AssistantMessage, TextContent

    store, execution = _admitted(tmp_path, execution_id=execution_id)
    request = TurnRequest(
        session_id=execution.session_id,
        user_text="durable agent turn",
        agent_id="default",
        source="component",
        user_msg_id="user-anchor",
    )
    request._execution_revision_id = execution.revision_id
    snapshot = _long_turn_snapshot(request)
    decision = AssistantMessage(
        content=[TextContent(text="saved")],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="stop",
    )
    assistant_dump = decision.model_dump(mode="json")
    completed_actions = [{"action_id": "provider-action", "input_hash": "context-hash"}]
    receipts = [{
        "effect_id": "effect-provider", "frontier_step_id": "provider:p",
        "action_id": "provider-action", "outcome": "committed",
        "receipt": {"provider_request_id": "saved-request"},
    }]
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
            "kind": "agent.provider.decision.after",
            "step_id": "after_provider:p",
            "phase": "after_provider",
            "sentinel": "resume-from-checkpoint",
        },
        frontier=[{"step_id": "after_provider:p", "phase": "after_provider", "branch_id": "main"}],
        turn={
            "user_message_id": "user-anchor",
            "assistant_message_id": "user-anchor_reply",
            "base_history_head_id": "user-anchor",
        },
        assistant_message=assistant_dump,
        tool_results=[],
        resolved_snapshot=snapshot,
        provider_action_id="provider-action",
        tool_call_ids=[],
        next_tool_index=0,
        repeat_failures={},
        completed_actions=completed_actions,
        terminal_effect_receipts=receipts,
        pending_messages=pending_messages,
        turn_display=turn_display,
    )
    for raw in state.blob_payloads.values():
        store.put_state_blob(execution.execution_id, raw)
    return store, execution, state

