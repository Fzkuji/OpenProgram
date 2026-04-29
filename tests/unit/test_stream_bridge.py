"""Unit tests for StreamBridge — the runtime→store translator."""
from __future__ import annotations

from openprogram.webui._stream_bridge import StreamBridge
from openprogram.webui.messages import MessageStore


def _setup():
    store = MessageStore()
    msg = store.create("c", "assistant", status="streaming")
    return store, msg, StreamBridge(store, msg.id)


def test_text_deltas_accumulate_into_one_block():
    store, msg, br = _setup()
    br.on_stream({"type": "text", "text": "hello "})
    br.on_stream({"type": "text", "text": "world"})
    text_blocks = [b for b in msg.content if b.type == "text"]
    assert len(text_blocks) == 1
    assert text_blocks[0].text == "hello world"


def test_thinking_creates_separate_block():
    store, msg, br = _setup()
    br.on_stream({"type": "thinking", "text": "hmm"})
    br.on_stream({"type": "text", "text": "answer"})
    assert [b.type for b in msg.content] == ["thinking", "text"]


def test_tool_use_and_result_share_block():
    store, msg, br = _setup()
    br.on_stream({"type": "tool_use", "tool_call_id": "t1",
                  "tool": "bash", "input": '{"cmd":"ls"}'})
    br.on_stream({"type": "tool_result", "tool_call_id": "t1",
                  "result": "file1\nfile2", "is_error": False})
    tools = [b for b in msg.content if b.type == "tool_use"]
    assert len(tools) == 1
    assert tools[0].tool_arguments == {"cmd": "ls"}
    assert tools[0].tool_result == "file1\nfile2"
    assert tools[0].tool_is_error is False


def test_multiple_parallel_tools_tracked_independently():
    store, msg, br = _setup()
    br.on_stream({"type": "tool_use", "tool_call_id": "a", "tool": "x"})
    br.on_stream({"type": "tool_use", "tool_call_id": "b", "tool": "y"})
    br.on_stream({"type": "tool_result", "tool_call_id": "b", "result": "R-b"})
    br.on_stream({"type": "tool_result", "tool_call_id": "a", "result": "R-a"})
    by_id = {b.tool_call_id: b for b in msg.content if b.type == "tool_use"}
    assert by_id["a"].tool_result == "R-a"
    assert by_id["b"].tool_result == "R-b"


def test_orphan_tool_result_synthesizes_block():
    store, msg, br = _setup()
    br.on_stream({"type": "tool_result", "tool_call_id": "zz",
                  "result": "out", "tool": "bash"})
    tools = [b for b in msg.content if b.type == "tool_use"]
    assert len(tools) == 1
    assert tools[0].tool_result == "out"


def test_commit_sets_complete_status_and_usage():
    store, msg, br = _setup()
    br.on_stream({"type": "text", "text": "hi"})
    br.commit(usage={"input_tokens": 5}, stop_reason="end_turn")
    assert msg.status == "complete"
    assert msg.usage == {"input_tokens": 5}
    assert msg.stop_reason == "end_turn"


def test_fail_sets_error():
    store, msg, br = _setup()
    br.fail("boom")
    assert msg.status == "error"
    assert msg.error == "boom"


def test_unknown_event_type_is_ignored():
    store, msg, br = _setup()
    br.on_stream({"type": "something_new", "data": 42})
    assert msg.seq == 0  # no mutations
