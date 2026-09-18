"""A checkpoint failure cannot discard a completed external operation."""
import asyncio
import json

import pytest

from openprogram.agent.agent_loop import _execute_tool_calls
from openprogram.agent.continuation import AgentCheckpointV1, AgentCheckpointError
from openprogram.agent.production_driver import AgentDriverError
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.execution.effects import EffectStore
from openprogram.execution.model import ExecutionStatus
from openprogram.providers.types import AssistantMessage, TextContent, ToolCall
from openprogram.providers.utils.event_stream import EventStream

from ._support import _prepare_long_turn


def test_tool_result_survives_checkpoint_serializer_failure(tmp_path, monkeypatch):
    store, _, _, _, _, snapshot, execution, _, hook = _prepare_long_turn(tmp_path, "receipt-recovery")
    calls = []
    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="mutation completed")], details={})
    tool = AgentTool(name="mutate", label="mutate", description="test", parameters={}, execute=execute)
    message = AssistantMessage(content=[ToolCall(id="once", name="mutate", arguments={})],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse")
    hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}})
    hook("provider.after", {"message": message.model_dump(mode="json"), "tool_call_ids": ["once"], "next_tool_index": 0})
    original = AgentCheckpointV1.build
    def failing(**kwargs):
        if kwargs["safe_point"]["phase"] == "after_tool":
            raise AgentCheckpointError("checkpoint_schema_invalid", "injected checkpoint failure")
        return original(**kwargs)
    monkeypatch.setattr(AgentCheckpointV1, "build", failing)
    async def safe_point(kind, payload):
        return hook(kind, payload)
    with pytest.raises(AgentDriverError, match="injected checkpoint failure"):
        asyncio.run(_execute_tool_calls([tool], message, None, EventStream(), None, {}, safe_point_hook=safe_point))
    assert len(calls) == 1
    assert EffectStore(store).list_unresolved(execution.execution_id) == []
    with store._connect() as connection:
        receipt = connection.execute("SELECT receipt_json FROM effects WHERE execution_id = ? AND json_extract(metadata_json, '$.kind') = 'tool.before'", (execution.execution_id,)).fetchone()
    import json
    assert json.loads(receipt[0])["checkpoint_pending"] is True


def test_owner_loss_recovery_does_not_reexecute_recorded_tool(tmp_path, monkeypatch):
    store, attempts, control, driver, request, snapshot, execution, active, hook = _prepare_long_turn(
        tmp_path, "receipt-resume",
    )
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="mutation completed")], details={})

    tool = AgentTool(name="mutate", label="mutate", description="test", parameters={}, execute=execute)
    message = AssistantMessage(
        content=[ToolCall(id="once", name="mutate", arguments={})],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}})
    hook("provider.after", {
        "message": message.model_dump(mode="json"), "tool_call_ids": ["once"], "next_tool_index": 0,
    })
    original = AgentCheckpointV1.build

    def failing(**kwargs):
        if kwargs["safe_point"]["phase"] == "after_tool":
            raise AgentCheckpointError("checkpoint_schema_invalid", "injected checkpoint failure")
        return original(**kwargs)

    monkeypatch.setattr(AgentCheckpointV1, "build", failing)

    async def safe_point(kind, payload):
        return hook(kind, payload)

    with pytest.raises(AgentDriverError, match="injected checkpoint failure"):
        asyncio.run(_execute_tool_calls(
            [tool], message, None, EventStream(), None, {}, safe_point_hook=safe_point,
        ))
    assert len(calls) == 1
    monkeypatch.setattr(AgentCheckpointV1, "build", original)
    recovered = control.recover_owner_loss(
        execution.execution_id, attempt_id=active.attempt_id, generation=active.generation,
    )
    current = store.get_execution(execution.execution_id)
    assert current.checkpoint_head_id is not None
    from openprogram.agent.continuation import AgentContinuation
    checkpoint = control.checkpoints.get(current.checkpoint_head_id)
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint, request=request,
    )
    assert continuation.next_tool_index == 1
    hook2 = driver._safe_point_hook(
        active, request, __import__("threading").Event(), continuation=continuation,
    )

    async def safe_point2(kind, payload):
        return hook2(kind, payload)

    asyncio.run(_execute_tool_calls(
        [tool], message, None, EventStream(), None, {},
        safe_point_hook=safe_point2, start_index=continuation.next_tool_index,
    ))
    assert len(calls) == 1
    assert recovered.execution.execution_id == execution.execution_id


def test_malformed_result_receipt_does_not_crash_owner_loss(tmp_path, monkeypatch):
    store, attempts, control, driver, request, snapshot, execution, active, hook = _prepare_long_turn(
        tmp_path, "receipt-corrupt",
    )

    async def execute(*args):
        return AgentToolResult(content=[TextContent(text="ok")], details={})

    tool = AgentTool(name="mutate", label="mutate", description="test", parameters={}, execute=execute)
    message = AssistantMessage(
        content=[ToolCall(id="once", name="mutate", arguments={})],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}})
    hook("provider.after", {
        "message": message.model_dump(mode="json"), "tool_call_ids": ["once"], "next_tool_index": 0,
    })
    original = AgentCheckpointV1.build

    def failing(**kwargs):
        if kwargs["safe_point"]["phase"] == "after_tool":
            raise AgentCheckpointError("checkpoint_schema_invalid", "injected checkpoint failure")
        return original(**kwargs)

    monkeypatch.setattr(AgentCheckpointV1, "build", failing)

    async def safe_point(kind, payload):
        return hook(kind, payload)

    with pytest.raises(AgentDriverError):
        asyncio.run(_execute_tool_calls(
            [tool], message, None, EventStream(), None, {}, safe_point_hook=safe_point,
        ))
    with store._connect() as connection:
        row = connection.execute(
            "SELECT receipt_json FROM effects WHERE execution_id = ? "
            "AND json_extract(metadata_json, '$.kind') = 'tool.before'",
            (execution.execution_id,),
        ).fetchone()
        receipt = json.loads(row[0])
        ref = receipt["agent_result_ref"]["ref"]
        connection.execute(
            "UPDATE execution_state_blobs SET payload = ? WHERE ref = ?",
            (b"{not-json", ref),
        )
        connection.commit()
    recovered = control.recover_owner_loss(
        execution.execution_id, attempt_id=active.attempt_id, generation=active.generation,
    )
    assert recovered.execution.status is ExecutionStatus.INTERRUPTED
    assert recovered.execution.reason_code == "recorded_result_invalid"


def test_permission_denial_is_not_started_not_dispatched(tmp_path):
    store, _, control, driver, request, snapshot, execution, active, hook = _prepare_long_turn(
        tmp_path, "not-started",
    )
    hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}})
    hook("provider.after", {
        "message": AssistantMessage(
            content=[ToolCall(id="deny", name="mutate", arguments={})],
            api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
        ).model_dump(mode="json"),
        "tool_call_ids": ["deny"], "next_tool_index": 0,
    })
    hook("tool.before", {"tool_call_id": "deny", "arguments": {}})
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status FROM effects WHERE execution_id = ? "
            "AND json_extract(metadata_json, '$.kind') = 'tool.before'",
            (execution.execution_id,),
        ).fetchone()
    assert row[0] == "planned"
    hook("tool.after", {
        "tool_call_id": "deny",
        "is_error": True,
        "result": {
            "role": "toolResult", "tool_call_id": "deny",
            "content": [{"type": "text", "text": "[denied]"}],
            "details": {"denied": True, "outcome": "not_started", "execution_started": False},
        },
        "next_tool_index": 1, "tool_call_ids": ["deny"],
    })
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status, receipt_json FROM effects WHERE execution_id = ? "
            "AND json_extract(metadata_json, '$.kind') = 'tool.before'",
            (execution.execution_id,),
        ).fetchone()
    assert row[0] == "not_committed"
    assert json.loads(row[1]).get("outcome") == "not_started"


@pytest.mark.parametrize("denied", [True, False])
def test_host_result_evidence_survives_recovery(tmp_path, monkeypatch, denied):
    store, attempts, control, driver, request, snapshot, execution, active, hook = _prepare_long_turn(
        tmp_path, "receipt-resume",
    )
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="mutation completed")], details={"outcome": "not_started", "execution_started": False})

    tool = AgentTool(name="mutate", label="mutate", description="test", parameters={}, execute=execute)
    if denied:
        from openprogram.agent.permissions.approval import wrap_with_approval
        monkeypatch.setattr("openprogram.agent.permissions.approval.permission_decision",
                            lambda *args: ("deny", "RULE_DENY", "test deny", None))
        tool = wrap_with_approval(tool, request, lambda _: None, _live=False)
    message = AssistantMessage(
        content=[ToolCall(id="once", name="mutate", arguments={})],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}})
    hook("provider.after", {
        "message": message.model_dump(mode="json"), "tool_call_ids": ["once"], "next_tool_index": 0,
    })
    original = AgentCheckpointV1.build

    def failing(**kwargs):
        if kwargs["safe_point"]["phase"] == "after_tool":
            raise AgentCheckpointError("checkpoint_schema_invalid", "injected checkpoint failure")
        return original(**kwargs)

    monkeypatch.setattr(AgentCheckpointV1, "build", failing)

    async def safe_point(kind, payload):
        return hook(kind, payload)

    with pytest.raises(AgentDriverError, match="injected checkpoint failure"):
        asyncio.run(_execute_tool_calls(
            [tool], message, None, EventStream(), None, {}, safe_point_hook=safe_point,
        ))
    assert len(calls) == (0 if denied else 1)
    monkeypatch.setattr(AgentCheckpointV1, "build", original)
    recovered = control.recover_owner_loss(
        execution.execution_id, attempt_id=active.attempt_id, generation=active.generation,
    )
    assert recovered.execution.status is not ExecutionStatus.RECONCILIATION_REQUIRED
    current = store.get_execution(execution.execution_id)
    assert current.checkpoint_head_id is not None
    from openprogram.agent.continuation import AgentContinuation
    checkpoint = control.checkpoints.get(current.checkpoint_head_id)
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint, request=request,
    )
    assert continuation.next_tool_index == 1
    hook2 = driver._safe_point_hook(
        active, request, __import__("threading").Event(), continuation=continuation,
    )

    async def safe_point2(kind, payload):
        return hook2(kind, payload)

    asyncio.run(_execute_tool_calls(
        [tool], message, None, EventStream(), None, {},
        safe_point_hook=safe_point2, start_index=continuation.next_tool_index,
    ))
    assert len(calls) == (0 if denied else 1)
    assert recovered.execution.execution_id == execution.execution_id
