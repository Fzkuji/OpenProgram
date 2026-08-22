"""GET /api/running snapshot — in-flight tool calls show up as kind=tool."""
import importlib
import time

agent_loop = importlib.import_module("openprogram.agent.agent_loop")
from openprogram_server._webui.routes import running


def test_collect_includes_running_tool_calls():
    call_id = "tc_test_running_snapshot"
    with agent_loop.RUNNING_TOOL_CALLS_LOCK:
        agent_loop.RUNNING_TOOL_CALLS[call_id] = {
            "tool_name": "bash",
            "label": "sleep 600",
            "started_at": time.time(),
        }
    try:
        items = running._collect()
        tool_items = [i for i in items if i["kind"] == "tool" and i["id"] == call_id]
        assert len(tool_items) == 1
        assert tool_items[0]["label"] == "sleep 600"
        assert tool_items[0]["tool_name"] == "bash"
        assert tool_items[0]["status"] == "running"
    finally:
        with agent_loop.RUNNING_TOOL_CALLS_LOCK:
            agent_loop.RUNNING_TOOL_CALLS.pop(call_id, None)


def test_tool_call_label_prefers_description_then_command():
    assert agent_loop._tool_call_label(
        "bash", {"command": "ls", "description": "List files"}) == "List files"
    assert agent_loop._tool_call_label("bash", {"command": "ls"}) == "ls"
    assert agent_loop._tool_call_label("read", {"path": "/tmp/x"}) == "/tmp/x"
    assert agent_loop._tool_call_label("weird", None) == "weird"
