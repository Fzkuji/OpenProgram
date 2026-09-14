"""Large tool payloads survive durable completion and fresh-store restoration."""
import pytest

from ._support import _prepare_long_turn, _provider_tool_decision, _committed_tool_ids
from openprogram.agent.continuation import AgentContinuation, AgentCheckpointError
from openprogram.execution.checkpoints import ExecutionCheckpointStore
from openprogram.execution.store import ExecutionStore


@pytest.mark.parametrize("kind", ["image", "text"])
def test_large_tool_result_commits_and_restores(tmp_path, kind):
    store, _, _, _, request, snapshot, execution, _, hook = _prepare_long_turn(
        tmp_path, "exec-large-result",
    )
    data = "A" * (2 * 1024 * 1024)
    content = ([{"type": "image", "data": data, "mime_type": "image/png"}]
               if kind == "image" else [{"type": "text", "text": data}])

    def large_result_hook(event, payload):
        if event == "tool.after":
            payload = {**payload, "result": {**payload["result"], "content": content, "tool_name": "web_use", "timestamp": 1}}
        return hook(event, payload)

    _provider_tool_decision(large_result_hook, snapshot, 0)
    assert len(_committed_tool_ids(store, execution.execution_id)) == 1
    fresh = ExecutionStore(store.path)
    current = fresh.get_execution(execution.execution_id)
    checkpoint = ExecutionCheckpointStore(fresh).get(current.checkpoint_head_id)
    restored = AgentContinuation.from_checkpoint(store=fresh, checkpoint=checkpoint, request=request)
    assert restored.next_tool_index == 1
    block = restored.tool_results[0].content[0]
    assert (block.data if kind == "image" else block.text) == data
    descriptor = restored.state.payload["tool_result_delta_refs"][0]
    assert descriptor["byte_length"] > 1024 * 1024
    assert any(action["result_ref"] == descriptor for action in restored.state.payload["completed_actions"])

    with fresh._transaction() as connection:
        connection.execute("UPDATE execution_state_blobs SET payload = ? WHERE execution_id = ? AND ref = ?",
                           (b"corrupt", execution.execution_id, descriptor["ref"]))
    with pytest.raises(AgentCheckpointError, match="integrity|corrupt"):
        AgentContinuation.from_checkpoint(store=fresh, checkpoint=checkpoint, request=request)
