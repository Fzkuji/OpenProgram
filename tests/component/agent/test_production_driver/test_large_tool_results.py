"""Large tool payloads survive in the journal and come back whole."""
import pytest

from ._support import _prepare_long_turn, _provider_tool_decision, _committed_tool_ids
from openprogram.runtime.conversation.checkpoint import AgentContinuation
from openprogram.runtime.execution.checkpoints import ExecutionCheckpointStore
from openprogram.runtime.execution.store import ExecutionStore


@pytest.mark.parametrize("kind", ["image", "text"])
def test_large_tool_result_is_journaled_and_restored(tmp_path, kind):
    store, _, control, _, request, snapshot, execution, active, hook = _prepare_long_turn(
        tmp_path, f"exec-large-{kind}",
    )
    data = "A" * (2 * 1024 * 1024)
    content = ([{"type": "image", "data": data, "mime_type": "image/png"}]
               if kind == "image" else [{"type": "text", "text": data}])

    def large_result_hook(event, payload):
        if event == "tool.after":
            payload = {**payload, "result": {**payload["result"], "content": content, "tool_name": "page_tool", "timestamp": 1}}
        return hook(event, payload)

    _provider_tool_decision(large_result_hook, snapshot, 0)
    assert len(_committed_tool_ids(store, execution.execution_id)) == 1
    control.publish_agent_position(
        attempt_id=active.attempt_id, generation=active.generation, fragment=hook._fragment(control),
    )
    fresh = ExecutionStore(store.path)
    current = fresh.get_execution(execution.execution_id)
    checkpoint = ExecutionCheckpointStore(fresh).get(current.checkpoint_head_id)
    restored = AgentContinuation.from_checkpoint(store=fresh, checkpoint=checkpoint, request=request)
    assert restored.next_tool_index == 1
    block = restored.tool_results[0].content[0]
    assert (block.data if kind == "image" else block.text) == data
