from __future__ import annotations


import asyncio


import json


import sqlite3




import threading




from types import SimpleNamespace




from openprogram.runtime.execution import AttemptStore, CapabilitySet, ExecutionStore






def _admitted(tmp_path, *, execution_id="exec-agent-1"):
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    AttemptStore(store)
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
        entrypoint="openprogram.runtime.conversation.turn_runtime:process_user_turn",
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
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver, CanonicalAgentEntry

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
    from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot
    from openprogram.models.providers.types import Model

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
            "name": "page_tool",
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
    assert hook("tool.started", {"tool_call_id": tool_call_id}) is False
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
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.execution.agent_journal import TOOL_RESULT

    session_id = store.get_execution(execution_id).session_id
    return [
        record["data"]["tool_call_id"]
        for record in default_db().read_turn_records(session_id, execution_id)
        if record["type"] == TOOL_RESULT
    ]


def _prepare_long_turn(tmp_path, execution_id):
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id=execution_id)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id=f"owner-{execution_id}",
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
