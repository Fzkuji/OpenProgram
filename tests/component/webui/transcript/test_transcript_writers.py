"""Writers record who owns a row: head, frames, send ids and origin."""
from __future__ import annotations

import json
import os
import threading
from types import SimpleNamespace

import pytest

from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.conversation.turn_runtime.stream_tap import (
    block_item_id,
    final_blocks,
    make_stream_tap,
    utf16_length,
)
from openprogram.state.store import SessionNodeWriter
from openprogram.state.store.session.memory_index import SessionMemoryIndex
from openprogram.state.store.session.nodes import Call
from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch, request):
    sessions = SessionStore(tmp_path / "sessions")
    request.addfinalizer(sessions.close)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: sessions)
    sessions.create_session("s", "main")
    writer = SessionNodeWriter(sessions, "s")
    writer.append(Call(id="u", role="user", output="hi"))
    writer.append(Call(id="u_reply", role="llm", output="", predecessor="u"))
    return sessions


def _head(store) -> str:
    return (store.get_session("s") or {})["head_id"]


def _attach_row(node_id="card", status="running", job_id="job-1", predecessor="u_reply", **attach):
    return {
        "id": node_id, "role": "assistant", "display": "runtime", "function": "attach",
        "content": "(running)", "predecessor": predecessor, "timestamp": 1,
        "extra": json.dumps({"attach": {
            "session_id": "s", "head_id": None, "status": status, "job_id": job_id, **attach,
        }}),
    }


# ---- head ------------------------------------------------------------

def test_an_attach_write_never_moves_the_head(store, monkeypatch):
    head = _head(store)
    moves = []
    real = store.set_head
    monkeypatch.setattr(store, "set_head", lambda *a, **k: (moves.append(a), real(*a, **k)))

    store.append_message("s", _attach_row(), advance_head=False)
    store.append_message("s", {
        "id": "notice", "role": "assistant", "display": "runtime", "function": "send_message",
        "content": "queued", "predecessor": head, "timestamp": 1,
    }, advance_head=False)

    assert moves == []
    assert _head(store) == head


def test_a_placeholder_with_no_head_is_closed_as_errored(store):
    from openprogram.runtime.conversation.peer_agents import sub_agent_run as module

    store.append_message("s", _attach_row(tool_call_id="call-1", delivery="tool_result"), advance_head=False)
    module.write_attach_pointer_for_spawn(
        session_id="s", caller_msg_id="u_reply",
        result=module.AgentTurnResult(failed=True, error="stopped"),
        label=None, prompt="p", chosen_agent="main", node_id="card",
    )

    node = next(n for n in store.get_nodes("s") if n.id == "card")
    assert node.metadata["attach"]["status"] == "errored"
    assert node.metadata["attach"]["job_id"] == "job-1"
    assert node.metadata["attach"]["tool_call_id"] == "call-1"
    assert node.output == "stopped" and node.metadata["is_error"] is True


@pytest.mark.parametrize("job_state,expected", [
    (None, "errored"), ("errored", "errored"), ("completed", "completed"), ("running", "running"),
])
def test_startup_closes_attach_rows_whose_job_is_not_active(store, monkeypatch, job_state, expected):
    from openprogram.runtime.execution.projections import dag

    store.append_message("s", _attach_row(), advance_head=False)
    job = None if job_state is None else SimpleNamespace(
        status=SimpleNamespace(value=job_state), head_id=None, result_text=None, error="worker died")
    records = SimpleNamespace(load=lambda _s, _j: job,
                              is_terminal=lambda j: j.status.value in {"errored", "completed"})
    monkeypatch.setattr(dag.hooks, "job_records", lambda: records)
    node = next(n for n in store.get_nodes("s") if n.id == "card")

    assert dag._close_stale_attach(node, "s", SessionNodeWriter(store, "s")) is (job_state != "running")
    node = next(n for n in store.get_nodes("s") if n.id == "card")
    assert node.metadata["attach"]["status"] == expected
    assert (node.output == "(running)") is (job_state == "running")


def test_an_ordinary_append_still_advances(store):
    store.append_message("s", {"id": "next", "role": "user", "content": "x",
                               "timestamp": 1, "predecessor": _head(store)})
    assert _head(store) == "next"


def test_render_context_ignores_a_running_sync_placeholder(store):
    from openprogram.runtime.context.session_dag.nodes import render_context
    from openprogram.runtime.context.session_dag.render import render_dag_messages

    def rendered():
        graph = SessionNodeWriter(store, "s").load()
        ids = render_context(graph, head_id=_head(store))
        return json.dumps([m.model_dump() for m in render_dag_messages(graph, ids)], default=str, sort_keys=True)

    before = rendered()
    store.append_message("s", _attach_row(delivery="tool_result"), advance_head=False)

    assert rendered() == before


# ---- index -----------------------------------------------------------

def test_a_rebuild_never_shows_a_reader_a_part_filled_index():
    index = SessionMemoryIndex()
    payloads = [{"id": f"n{i}", "seq": i, "role": "user", "output": ""} for i in range(3)]
    index.rebuild(payloads, {}, lambda _p: None, lambda _p: None)
    seen: list[int] = []

    def observe(_payload):
        seen.append(len(index.nodes_by_seq))

    index.rebuild(payloads + [{"id": "n3", "seq": 3, "role": "user", "output": ""}], {}, observe, lambda _p: None)

    assert seen == [3, 3, 3, 3]
    assert len(index.nodes_by_seq) == 4


# ---- stream ----------------------------------------------------------

def _frame(event, msg_id="u", session_id="s"):
    return {"type": "chat_response", "data": {
        "type": "stream_event", "session_id": session_id, "msg_id": msg_id, "event": event}}


def _tap(store, on_event):
    req = TurnRequest(session_id="s", user_text="hi", agent_id="main", source="web", user_msg_id="u")
    return make_stream_tap(on_event=on_event, req=req, assistant_msg_id="u_reply",
                           placeholder_inserted=True, agentic_tool_names=set())


def _stored_text(store):
    node = next(n for n in store.get_nodes("s") if n.id == "u_reply")
    return node.output


def test_the_stored_row_is_at_or_ahead_of_every_frame(store):
    seen = []
    shown = ""

    def on_event(env):
        nonlocal shown
        shown += env["data"]["event"]["text"]
        seen.append((shown, _stored_text(store)))

    tap = _tap(store, on_event)
    for piece in ("Hel", "lo ", "there"):
        tap(_frame({"type": "text", "text": piece}))

    assert seen == [("Hel", "Hel"), ("Hello ", "Hello "), ("Hello there", "Hello there")]


def test_a_marker_the_model_streams_is_stored_as_its_name(store):
    """The row's text mid-turn is what a reply cut off by a worker restart
    keeps; a marker in it was never recorded, so it reads the name, even
    split across frames. The live frames and blocks are left as streamed."""
    from openprogram.state.attachments import format_marker
    sent = []
    tap = _tap(store, sent.append)
    forged = format_marker("id_rsa", "/home/me/.ssh/id_rsa", 1)
    tap(_frame({"type": "text", "text": "Here: " + forged[:20]}))
    tap(_frame({"type": "text", "text": forged[20:] + " ok"}))

    assert _stored_text(store) == "Here: id_rsa ok"
    assert "".join(f["data"]["event"]["text"] for f in sent) == "Here: " + forged + " ok"


def test_text_frames_name_their_item_and_utf16_offset(store):
    sent = []
    tap = _tap(store, sent.append)
    tap(_frame({"type": "thinking", "text": "plan"}))
    tap(_frame({"type": "text", "text": "a\U0001F600"}))
    tap(_frame({"type": "text", "text": "b"}))
    tap(_frame({"type": "tool_use", "tool": "read", "tool_call_id": "t1", "input": {}}))
    tap(_frame({"type": "text", "text": "c"}))

    fields = [(f["data"].get("row_id"), f["data"].get("item_id"), f["data"].get("item_index"),
               f["data"].get("offset")) for f in sent]
    assert fields == [
        ("u_reply", "thinking:0", 0, 0),
        ("u_reply", "text:0", 1, 0),
        ("u_reply", "text:0", 1, 3),
        (None, None, None, None),
        ("u_reply", "text:1", 3, 0),
    ]


def _stored_blocks(store):
    node = next(n for n in store.get_nodes("s") if n.id == "u_reply")
    extra = (node.metadata or {}).get("extra") or "{}"
    return json.loads(extra)["blocks"] if isinstance(extra, str) else extra["blocks"]


def test_a_restarted_response_leaves_the_row_before_the_new_one_arrives(store):
    sent = []
    tap = _tap(store, sent.append)
    tap(_frame({"type": "text", "text": "first answer"}))
    tap(_frame({"type": "tool_use", "tool": "read", "tool_call_id": "t1", "input": {}}))
    tap(_frame({"type": "tool_result", "tool": "read", "tool_call_id": "t1", "result": "ok"}))
    # The next response streams reasoning and text, stalls, and is sent again.
    tap(_frame({"type": "thinking", "text": "Inspecting"}))
    tap(_frame({"type": "text", "text": "I will"}))
    tap(_frame({"type": "retry", "attempt": 2, "reason": "transport", "restart": True}))

    assert [b["type"] for b in tap.blocks] == ["text", "tool"]
    assert _stored_text(store) == "first answer"
    assert [b["type"] for b in _stored_blocks(store)] == ["text", "tool"]

    tap(_frame({"type": "text", "text": "done"}))
    assert [b.get("text") for b in tap.blocks if b["type"] == "text"] == ["first answer", "done"]
    # The new text starts its block: nothing is appended to the dropped one.
    assert (sent[-1]["data"]["item_index"], sent[-1]["data"]["offset"]) == (2, 0)
    # A tool call came between the two passages (``reply_text``).
    assert _stored_text(store) == "first answer\n\ndone"


def test_a_restart_cuts_only_the_response_being_sent_again(store):
    tap = _tap(store, lambda env: None)
    # A response that ran out of output is continued by the next one, which
    # adds to the same text block; a restart of the second keeps the first.
    tap(_frame({"type": "text", "text": "part one"}))
    tap(_frame({"type": "retry", "attempt": 2, "reason": "output_limit"}))
    tap(_frame({"type": "text", "text": ", part tw"}))
    tap(_frame({"type": "retry", "attempt": 2, "reason": "transport", "restart": True}))
    assert [b["text"] for b in tap.blocks] == ["part one"]

    # A reconnect before any content (no ``restart``) cuts nothing.
    tap(_frame({"type": "text", "text": ", part two"}))
    tap(_frame({"type": "retry", "attempt": 2, "reason": "transport"}))
    assert [b["text"] for b in tap.blocks] == ["part one, part two"]


def test_a_frame_of_a_child_row_gets_no_item_fields(store):
    sent = []
    _tap(store, sent.append)(_frame({"type": "text", "text": "x"}, msg_id="child", session_id="other"))
    assert "row_id" not in sent[0]["data"]


def test_citations_name_the_block_they_land_on(store):
    sent = []
    tap = _tap(store, sent.append)
    tap(_frame({"type": "text", "text": "a"}))
    tap(_frame({"type": "text_end", "citations": [{"url": "u"}]}))
    assert (sent[1]["data"]["row_id"], sent[1]["data"]["item_id"]) == ("u_reply", "text:0")


def test_a_failed_write_still_emits(store, monkeypatch):
    sent = []
    tap = _tap(store, sent.append)
    monkeypatch.setattr(store, "update_stream_progress",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    tap(_frame({"type": "text", "text": "x"}))
    assert sent[0]["data"]["offset"] == 0


def test_text_is_written_without_fsync_and_tools_with_it(store, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd)))
    tap = _tap(store, lambda _env: None)

    tap(_frame({"type": "text", "text": "x"}))
    assert calls == []
    tap(_frame({"type": "tool_use", "tool": "read", "tool_call_id": "t1", "input": {}}))
    assert calls


def test_item_ids_follow_runs():
    blocks = [{"type": "text"}, {"type": "text"}, {"type": "tool"}, {"type": "text"}]
    assert [block_item_id(blocks, i) for i in (0, 1, 3)] == ["text:0", "text:0.1", "text:1"]
    assert utf16_length("a\U0001F600") == 3


def _queued(data: dict):
    from openprogram.interfaces.server.ws_delivery import _Frame

    payload = json.dumps({"type": "chat_response", "data": {
        "type": "stream_event", "event": {"type": "text", "text": data.pop("text")}, **data}})
    return _Frame(payload=payload, size=len(payload), kind="append", key=("k",),
                  enqueued_at=0.0, waiters=[])


def test_queued_text_merges_only_when_contiguous():
    from openprogram.interfaces.server.ws_delivery import _merge_adjacent

    first = dict(row_id="r", item_id="text:0", offset=0, text="a\U0001F600")
    merged = _merge_adjacent(_queued(dict(first)), _queued(dict(row_id="r", item_id="text:0", offset=3, text="b")))
    assert json.loads(merged.payload)["data"]["event"]["text"] == "a\U0001F600b"
    assert _merge_adjacent(_queued(dict(first)), _queued(dict(row_id="r", item_id="text:0", offset=9, text="b"))) is None
    assert _merge_adjacent(_queued(dict(first)), _queued(dict(row_id="r", item_id="text:1", offset=3, text="b"))) is None


# ---- final blocks ----------------------------------------------------

# The loop's ordered blocks win, on a Stop too: they hold the cut response
# (``loop_runner._keep_stopped_response``). The tap's trace stands in only
# when they hold nothing.
@pytest.mark.parametrize("cancelled,ordered,expected", [
    (True, [{"type": "text", "text": "done"}], "done"),
    (True, [], "done and partial"),
    (False, [{"type": "text", "text": "done"}], "done"),
    (False, [], "done and partial"),
])
def test_final_blocks_keep_what_was_received(store, cancelled, ordered, expected):
    tap = _tap(store, lambda _env: None)
    tap(_frame({"type": "text", "text": "done and partial"}))
    stop = threading.Event()
    if cancelled:
        stop.set()

    assert final_blocks(tap, ordered, stop)[0]["text"] == expected


def test_a_stop_after_a_finished_message_stores_the_partial_text(store):
    from openprogram.runtime.conversation.turn_runtime.persistence import persist_assistant_message

    tap = _tap(store, lambda _env: None)
    tap(_frame({"type": "text", "text": "finished"}))
    tap(_frame({"type": "tool_use", "tool": "read", "tool_call_id": "t1", "input": {}}))
    tap(_frame({"type": "text", "text": "partial"}))
    stop = threading.Event()
    stop.set()
    req = TurnRequest(session_id="s", user_text="hi", agent_id="main", source="web", user_msg_id="u")
    # What the loop holds after the Stop: the finished response, then the cut
    # one's text as one last unsigned response, and the text of both.
    ordered = [
        {"type": "text", "text": "finished"},
        {"type": "text", "text": "partial", "partial": True, "round_start": True},
    ]

    msg, blocks, _tools, _usage = persist_assistant_message(
        db=store, req=req, session=store.get_session("s"), usage={}, final_text="finishedpartial",
        history=[], tool_calls=[], _ordered_blocks=final_blocks(tap, ordered, stop),
        _agentic_tool_names=set(), _placeholder_inserted=True, cancel_event=stop,
        assistant_msg_id="u_reply", user_msg_id="u",
    )

    assert [b.get("text") for b in blocks if b["type"] == "text"] == ["finished", "partial"]
    # The stored text is the turn's text as the loop returned it.
    assert msg["content"] == "finishedpartial"


# ---- ids and origin --------------------------------------------------

def _chat_request(cmd):
    return SimpleNamespace(
        text="typed", cmd=cmd, user_attachments=None, attachments=None, recording_meta=None,
    )


def test_a_web_user_row_names_its_send_and_is_a_person(monkeypatch):
    from openprogram.interfaces.server.ws_actions.chat import turn

    monkeypatch.setattr(turn, "_skill_model_text", lambda _req: None)
    row = turn._build_user_msg(_chat_request({"client_request_id": "req-1"}), "m", {"action": "chat"}, {})
    assert (row["client_request_id"], row["origin"]) == ("req-1", "person")


def test_an_invalid_send_id_is_not_stored(monkeypatch):
    from openprogram.interfaces.server.ws_actions.chat import turn

    monkeypatch.setattr(turn, "_skill_model_text", lambda _req: None)
    row = turn._build_user_msg(_chat_request({"client_request_id": 7}), "m", {"action": "chat"}, {})
    assert "client_request_id" not in row


def test_function_http_forwards_the_send_id(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes import chat as routes_chat

    app = FastAPI()
    routes_chat.register(app)
    seen = {}

    def run(name, kwargs, session_id, **options):
        seen.update(kwargs=kwargs, options=options)
        return {"session_id": "s", "msg_id": "m"}

    monkeypatch.setattr(routes_chat, "run_agent_method_call", run)
    assert TestClient(app).post("/api/function/demo", json={"value": 1, "client_request_id": "req-2"}).status_code == 200
    assert seen["options"]["client_request_id"] == "req-2"
    assert seen["kwargs"] == {"value": 1}


def test_a_local_command_forwards_the_send_id(monkeypatch):
    import asyncio
    from openprogram.interfaces.server.ws_actions.chat import turn_local_command as local
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _client_request_id

    seen = {}

    def run(name, kwargs, session_id, **options):
        seen.update(options)
        return {"session_id": "s", "msg_id": "m", "execution_id": "e"}

    monkeypatch.setattr("openprogram.interfaces.server.routes.chat.run_agent_method_call", run)

    class _Socket:
        async def send_text(self, _text):
            return None

    async def go():
        token = _client_request_id.set("req-3")
        try:
            await local._run_local_invocation(_Socket(), {"name": "demo", "kwargs": {}}, "s", None)
        finally:
            _client_request_id.reset(token)

    asyncio.run(go())
    assert seen["client_request_id"] == "req-3"


@pytest.mark.parametrize("fields,expected", [
    ({"message_origin": "agent_delivery", "speaker_kind": "runtime", "source": "agent_spawn"}, "agent_delivery"),
    ({"speaker_kind": "owner", "source": "tui"}, "person"),
    ({"speaker_kind": "human", "source": "wechat"}, "person"),
    ({"speaker_kind": "runtime", "source": "agent_spawn"}, None),
    ({"speaker_kind": "owner", "source": "job_followup"}, None),
])
def test_a_runtime_written_user_row_records_who_wrote_it(store, monkeypatch, fields, expected):
    from openprogram.runtime.conversation.turn_runtime import prep

    written = []
    writer = SimpleNamespace(persist_user=lambda _id, msg, _caller: written.append(msg))
    req = TurnRequest(session_id="s", user_text="[task from x:y] hello", agent_id="main",
                      history_override=[], **fields)

    prep.prepare_turn(db=store, req=req, writer=writer, user_msg_id="new", on_event=lambda _e: None)

    assert written[0].get("origin") == expected


def test_a_delivery_carries_its_origin_through_the_job():
    from openprogram.runtime.jobs.input import turn_request_fields
    from openprogram.runtime.jobs.types import Job

    assert "message_origin" in turn_request_fields()
    restored = Job.from_dict(Job(id="j", parent_session_id="s", prompt="p", agent_id="main",
                                 message_origin="agent_delivery").to_dict())
    assert restored.message_origin == "agent_delivery"


def _reopened_reply(store):
    """The reply as a process that never saw it written reads it from the log."""
    reread = SessionStore(store.root_path)
    try:
        return next(n for n in reread.get_nodes("s") if n.id == "u_reply")
    finally:
        reread.close()


def test_a_long_reply_writes_what_it_added_not_its_whole_trace_again(store):
    """Each write used to repeat the reply's whole trace, so a reply with
    many deltas and large tool results wrote hundreds of times its size."""
    import random
    noise = random.Random(7)
    tap = _tap(store, lambda _env: None)
    for call in range(40):
        for piece in range(30):
            tap(_frame({"type": "text", "text": f"step {call}.{piece} "}))
        tid = f"t{call}"
        tap(_frame({"type": "tool_use", "tool": "read", "tool_call_id": tid, "input": {"n": call}}))
        tap(_frame({"type": "tool_result", "tool": "read", "tool_call_id": tid,
                    "result": "r" * 2000, "full_output": noise.randbytes(10000).hex()}))
    tap.flush()

    live = next(n for n in store.get_nodes("s") if n.id == "u_reply")
    trace = len(live.metadata["extra"])
    log = store._session_dir("s") / "main.jsonl"
    # The tool nodes hold each result again; the trace itself is written
    # about once, not once per delta.
    assert log.stat().st_size < 4 * trace
    reopened = _reopened_reply(store)
    assert (reopened.output, reopened.metadata["extra"]) == (live.output, live.metadata["extra"])


def test_every_kind_of_change_reads_back_as_the_reply_held_it(store):
    tap = _tap(store, lambda _env: None)
    tap(_frame({"type": "thinking", "text": "plan"}))
    tap(_frame({"type": "text", "text": "Hello"}))
    tap(_frame({"type": "text", "text": " world"}))
    tap(_frame({"type": "text_end", "citations": [{"url": "u"}]}))
    tap(_frame({"type": "tool_use", "tool": "read", "tool_call_id": "a", "input": {}}))
    tap(_frame({"type": "retry", "reason": "provider"}))
    tap(_frame({"type": "text", "text": "cut"}))
    # The request goes out again: the text since the last response ends.
    tap(_frame({"type": "retry", "restart": True}))
    tap(_frame({"type": "text", "text": "again"}))
    tap(_frame({"type": "tool_result", "tool": "read", "tool_call_id": "a", "result": "ok"}))
    tap({"type": "chat_response", "data": {
        "type": "user_message", "steering": True, "assistant_msg_id": "u_reply",
        "session_id": "s", "msg_id": "steer-1", "content": "also this"}})
    tap(_frame({"type": "web_search", "provider_executed": True}))
    tap(_frame({"type": "tool_result", "tool": "web_search", "tool_call_id": "w",
                "provider_executed": True, "input": {"q": "x"}, "result": "found"}))
    tap(_frame({"type": "text", "text": "done [file: a.txt]"}))
    tap.flush()

    live = next(n for n in store.get_nodes("s") if n.id == "u_reply")
    reopened = _reopened_reply(store)
    assert (reopened.output, json.loads(reopened.metadata["extra"])) == (
        live.output, json.loads(live.metadata["extra"]))
    kinds = [block["type"] for block in json.loads(live.metadata["extra"])["blocks"]]
    assert kinds[:3] == ["thinking", "text", "tool"] and "steering" in kinds


def test_a_reply_cut_off_keeps_each_finished_passage_on_one_log_line(store):
    """Cross-conversation search greps the log. A reply that never got its
    closing write (the worker died) must still be found by words that
    arrived in different deltas."""
    tap = _tap(store, lambda _env: None)
    for piece in ("the quar", "terly rep", "ort is ready"):
        tap(_frame({"type": "text", "text": piece}))
    tap(_frame({"type": "tool_use", "tool": "read", "tool_call_id": "a", "input": {}}))

    log = (store._session_dir("s") / "main.jsonl").read_text(encoding="utf-8")
    assert "the quarterly report is ready" in log
    live = next(n for n in store.get_nodes("s") if n.id == "u_reply")
    reopened = _reopened_reply(store)
    assert (reopened.output, json.loads(reopened.metadata["extra"])) == (
        live.output, json.loads(live.metadata["extra"]))
