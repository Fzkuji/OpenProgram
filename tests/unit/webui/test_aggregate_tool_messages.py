from __future__ import annotations

import json

from openprogram.interfaces.server.persistence import aggregate_tool_messages
from openprogram.state.store import SessionStore


def _tool(row_id, name, *, call_id=None):
    tool_use = {"name": name, "arguments": {}, "caller": "a1"}
    if call_id:
        tool_use["tool_call_id"] = call_id
    return {"id": row_id, "role": "tool", "function": name, "caller": "a1", "content": "out",
            "extra": json.dumps({"tool_use": tool_use})}


def test_a_call_attached_after_the_turn_is_shown_once_after_its_blocks():
    assistant = {"id": "a1", "role": "assistant", "content": "done", "extra": json.dumps({"blocks": [
        {"type": "text", "text": "running"},
        {"type": "tool", "tool": "bash", "tool_call_id": "call_1", "input": "{}", "result": "out"},
    ]})}
    rows = [assistant, _tool("a1_t_call_1", "bash", call_id="call_1"), _tool("su_result_x", "self_update_result")]

    [message] = aggregate_tool_messages(rows)

    tools = [(b["tool"], b["tool_call_id"]) for b in message["blocks"] if b["type"] == "tool"]
    assert tools == [("bash", "call_1"), ("self_update_result", "su_result_x")]


def _flattened_reply(tmp_path):
    """A reply written by the append fallback: its ``extra`` becomes top-level fields."""
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s", "main")
    store.append_message("s", {"id": "u1", "role": "user", "content": "go"})
    call = {"tool_call_id": "call_1", "tool": "bash", "input": "{}", "result": "out", "is_error": False}
    store.append_message("s", {
        "id": "a1", "role": "assistant", "content": "done", "predecessor": "u1",
        "extra": json.dumps({"tool_calls": [call], "blocks": [{"type": "tool", **call}]}),
    })
    store.append_message("s", {**_tool("a1_t_call_1", "bash", call_id="call_1"), "predecessor": ""})
    store.append_message("s", {**_tool("su_result_x", "self_update_result"), "predecessor": ""})
    return store


def _reply(store):
    return next(m for m in aggregate_tool_messages(store.get_messages("s")) if m["id"] == "a1")


def test_a_reply_with_no_extra_shows_a_call_attached_after_the_turn(tmp_path):
    reply = _reply(_flattened_reply(tmp_path))

    assert "extra" not in reply
    assert [(b["tool"], b["tool_call_id"]) for b in reply["blocks"]] == [
        ("bash", "call_1"), ("self_update_result", "su_result_x"),
    ]


def test_loading_a_reply_again_does_not_add_tool_calls(tmp_path):
    store = _flattened_reply(tmp_path)

    loads = [[call["tool_call_id"] for call in _reply(store)["tool_calls"]] for _ in range(3)]

    assert loads == [["a1_t_call_1", "su_result_x"]] * 3
    stored = next(m for m in store.get_messages("s") if m["id"] == "a1")
    assert [call["tool_call_id"] for call in stored["tool_calls"]] == ["call_1"]


def test_a_repeated_call_id_replaces_each_stored_call_in_turn():
    stored = [{"tool_call_id": "call_1", "tool": "a"}, "not a call", {"tool_call_id": "call_1", "tool": "b"}]
    rows = [{"id": "a1", "role": "assistant", "content": "", "tool_calls": stored},
            _tool("r1", "x", call_id="call_1"), _tool("r2", "y", call_id="call_1"), _tool("r3", "z", call_id="call_1")]

    [reply] = aggregate_tool_messages(rows)

    assert [c if isinstance(c, str) else c["tool_call_id"] for c in reply["tool_calls"]] == ["r1", "not a call", "r2", "r3"]
    assert stored[0]["tool"] == "a"


def test_folding_reads_each_stored_call_once_however_many_rows_a_reply_has():
    class Counted(dict):
        reads = 0

        def get(self, *args):
            Counted.reads += 1
            return super().get(*args)

    count = 500
    stored = [Counted(tool_call_id=f"call_{i}") for i in range(count)]
    rows = [{"id": "a1", "role": "assistant", "content": "", "tool_calls": stored},
            *[_tool(f"a1_t_call_{i}", "x", call_id=f"call_{i}") for i in range(count)]]

    [reply] = aggregate_tool_messages(rows)

    assert Counted.reads == count
    assert [call["tool_call_id"] for call in reply["tool_calls"]] == [f"a1_t_call_{i}" for i in range(count)]
