"""Repeated tool errors remain durable through the real loop and driver."""
from __future__ import annotations

import asyncio

from openprogram.agent.agent_loop import _execute_tool_calls
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.execution.effects import EffectStore
from openprogram.providers.types import AssistantMessage, TextContent, ToolCall
from openprogram.providers.utils.event_stream import EventStream

from ._support import _committed_tool_ids, _prepare_long_turn


def test_distinct_rejections_commit_results_without_reconciliation(tmp_path):
    store, _, _, _, _, snapshot, execution, _, hook = _prepare_long_turn(
        tmp_path, "failure-checkpoints",
    )

    async def execute(call_id, args, cancel, on_update):
        return AgentToolResult(
            content=[TextContent(text="permission denied")],
            details={"denied": True, "reason_code": "AUTO_RISK_DENY"}, is_error=True,
        )

    tool = AgentTool(name="bash", description="shell", parameters={}, label="bash", execute=execute)
    failures = {}

    async def safe_point(kind, payload):
        return hook(kind, payload)

    async def run():
        for i in range(24):
            call_id = f"denied-{i}"
            message = AssistantMessage(
                content=[ToolCall(id=call_id, name="bash", arguments={"command": f"command-{i}"})],
                api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
            )
            hook("provider.before", {
                "resolved_snapshot": snapshot, "context": {"messages": [str(i)]},
                "supports_idempotency_key": True,
            })
            hook("provider.after", {
                "message": message.model_dump(mode="json"), "tool_call_ids": [call_id], "next_tool_index": 0,
            })
            await _execute_tool_calls(
                [tool], message, None, EventStream(), None, failures, safe_point_hook=safe_point,
            )
    asyncio.run(run())
    assert len(_committed_tool_ids(store, execution.execution_id)) == 24
    assert EffectStore(store).list_unresolved(execution.execution_id) == []
    assert store.get_execution(execution.execution_id).reason_code is None
