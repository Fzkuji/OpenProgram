"""A reply's blocks carry the task and step they belong to, live and stored."""
from __future__ import annotations

import json
import threading

import pytest

from openprogram.interfaces.server.transcript.blocks import project_blocks
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.conversation.event_parsing import STORED_DETAILS_KEY
from openprogram.runtime.conversation.turn_runtime import stream_tap
from openprogram.runtime.conversation.turn_runtime.stream_tap import final_blocks, make_stream_tap
from openprogram.state.store import SessionNodeWriter
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


def _frame(event):
    return {"type": "chat_response", "data": {
        "type": "stream_event", "session_id": "s", "msg_id": "u", "event": event}}


def _req():
    return TurnRequest(session_id="s", user_text="hi", agent_id="main", source="web", user_msg_id="u")


def _tap(on_event=lambda _env: None):
    return make_stream_tap(on_event=on_event, req=_req(), assistant_msg_id="u_reply",
                           placeholder_inserted=True, agentic_tool_names=set())


def _use(tap, tool, call_id):
    tap(_frame({"type": "tool_use", "tool": tool, "tool_call_id": call_id, "input": "{}"}))


def _result(tap, tool, call_id, effect=None):
    event = {"type": "tool_result", "tool": tool, "tool_call_id": call_id, "result": "ok"}
    if effect is not None:
        event[STORED_DETAILS_KEY] = {"task": effect}
    tap(_frame(event))


def _stored(store):
    node = next(n for n in store.get_nodes("s") if n.id == "u_reply")
    extra = (node.metadata or {}).get("extra") or "{}"
    return json.loads(extra) if isinstance(extra, str) else extra


def _owners(blocks):
    return [(b.get("tool") or b["type"], b.get("task"), b.get("step")) for b in blocks]


def test_blocks_are_stamped_by_position_as_they_stream(store):
    sent = []
    tap = _tap(sent.append)
    tap(_frame({"type": "text", "text": "I'll look first."}))
    _use(tap, "task_start", "c1")
    _result(tap, "task_start", "c1", {"task": "T3", "step": "14"})
    _use(tap, "grep", "c2")
    _result(tap, "grep", "c2")
    tap(_frame({"type": "text", "text": "The list is in agent-list.tsx."}))
    _use(tap, "task_step", "c3")
    _use(tap, "edit", "c4")  # issued in the same response, before the step call returned
    assert _owners(tap.blocks)[-1] == ("edit", "T3", "14")
    _result(tap, "task_step", "c3", {"task": "T3", "step": "15"})
    _result(tap, "edit", "c4")
    _use(tap, "task_step", "c9")
    _result(tap, "task_step", "c9", {"task": None})
    tap(_frame({"type": "text", "text": "Done."}))
    tap.flush()

    expected = [
        ("text", None, None), ("task_start", "T3", "14"), ("grep", "T3", "14"), ("text", "T3", "14"),
        ("task_step", "T3", "15"), ("edit", "T3", "15"), ("task_step", None, None), ("text", None, None),
    ]
    assert _owners(tap.blocks) == expected
    stored = _stored(store)["blocks"]
    assert _owners(stored) == expected
    assert stored[1]["task_effect"] == {"task": "T3", "step": "14"}
    assert stored[6]["task_effect"] == {"task": None}
    # A text frame names what its block belongs to; one outside a task names nothing.
    texts = [env["data"] for env in sent if env["data"].get("event", {}).get("type") == "text"]
    assert [(d.get("task"), d.get("step")) for d in texts] == [(None, None), ("T3", "14"), (None, None)]
    assert "task" not in texts[0]
    # What goes on the wire has the stamps and never the effect.
    drawn, _tools, _thinking = project_blocks("u_reply", stored)
    assert [(b.get("tool") or b["type"], b.get("task"), b.get("step")) for b in drawn] == expected
    assert not any("task_effect" in block for block in drawn)
    # The details stay with the tool node, not on the frame a client gets.
    assert not any(STORED_DETAILS_KEY in env["data"].get("event", {}) for env in sent)
    node = next(n for n in store.get_nodes("s") if n.id == "u_reply_t_c1")
    assert node.metadata["details"] == {"task": {"task": "T3", "step": "14"}}


def test_a_restarted_response_gives_the_step_back(store):
    tap = _tap()
    _use(tap, "task_start", "c1")
    _result(tap, "task_start", "c1", {"task": "T1", "step": "1"})
    tap(_frame({"type": "text", "text": "partial"}))
    tap(_frame({"type": "retry", "attempt": 2, "reason": "transport", "restart": True}))
    tap(_frame({"type": "text", "text": "again"}))
    assert _owners(tap.blocks) == [("task_start", "T1", "1"), ("text", "T1", "1")]


def test_a_resumed_reply_goes_on_where_its_stored_blocks_end(store):
    first = _tap()
    _use(first, "task_start", "c1")
    _result(first, "task_start", "c1", {"task": "T1", "step": "1"})
    first.flush()
    resumed = _tap()
    _use(resumed, "edit", "c2")
    assert _owners(resumed.blocks) == [("task_start", "T1", "1"), ("edit", "T1", "1")]


def test_a_goal_turn_starts_in_the_open_task_and_opens_its_segment_at_the_first_real_call(store, monkeypatch):
    from openprogram.programs.tools.planning.todo import shared

    opened = []
    monkeypatch.setattr(stream_tap, "_starting_owner", lambda req: {"task": "T2", "step": "20"})
    monkeypatch.setattr(shared, "open_turn_segment",
                        lambda sid, anchor, task, **where: opened.append((sid, anchor, task, where["scope"])))
    tap = _tap()
    assert tap.initial == {"task": "T2", "step": "20"}
    tap(_frame({"type": "thinking", "text": "where was I"}))
    assert opened == []
    _use(tap, "read", "c1")
    _use(tap, "read", "c2")
    assert opened == [("s", "u_reply", "T2", "")]
    assert _owners(tap.blocks) == [("thinking", "T2", "20"), ("read", "T2", "20"), ("read", "T2", "20")]
    tap.flush()
    # A resumed execution reads what the reply started with instead of asking again.
    assert _stored(store)["task_initial"] == {"task": "T2", "step": "20"}
    monkeypatch.setattr(stream_tap, "_starting_owner", lambda req: None)
    assert _tap().initial == {"task": "T2", "step": "20"}


def test_a_step_call_first_in_a_goal_turn_writes_the_segment_itself(store, monkeypatch):
    from openprogram.programs.tools.planning.todo import shared

    opened = []
    monkeypatch.setattr(stream_tap, "_starting_owner", lambda req: {"task": "T2"})
    monkeypatch.setattr(shared, "open_turn_segment", lambda *a, **k: opened.append(a))
    tap = _tap()
    _use(tap, "task_step", "c1")
    _result(tap, "task_step", "c1", {"task": "T2", "step": "21"})
    _use(tap, "edit", "c2")
    assert opened == []
    assert _owners(tap.blocks) == [("task_step", "T2", "21"), ("edit", "T2", "21")]


def test_a_reply_without_a_task_is_stored_as_before(store):
    tap = _tap()
    tap(_frame({"type": "text", "text": "hello"}))
    _use(tap, "read", "c1")
    _result(tap, "read", "c1")
    tap.flush()
    stored = _stored(store)
    assert "task_initial" not in stored
    assert all(not {"task", "step", "task_effect"} & set(block) for block in stored["blocks"])


def test_the_final_blocks_are_stamped_from_what_the_tap_saw(store):
    from openprogram.runtime.conversation.turn_runtime.persistence import persist_assistant_message

    tap = _tap()
    _use(tap, "task_start", "c1")
    _result(tap, "task_start", "c1", {"task": "T1", "step": "1"})
    _use(tap, "edit", "c2")
    _result(tap, "edit", "c2")
    # The loop's own blocks: the same calls, text cut differently, no stamps.
    ordered = [
        {"type": "text", "text": "Starting.", "round_start": True},
        {"type": "tool", "tool": "task_start", "tool_call_id": "c1", "input": "{}"},
        {"type": "tool", "tool": "edit", "tool_call_id": "c2", "input": "{}"},
        {"type": "text", "text": "Edited."},
    ]
    stop = threading.Event()
    chosen = final_blocks(tap, ordered, stop)
    assert _owners(chosen) == [("text", None, None), ("task_start", "T1", "1"), ("edit", "T1", "1"),
                               ("text", "T1", "1")]
    calls = [{"tool_call_id": "c1", "tool": "task_start", "result": "ok", "is_error": False},
             {"tool_call_id": "c2", "tool": "edit", "result": "ok", "is_error": False}]
    _msg, blocks, _tools, _usage = persist_assistant_message(
        db=store, req=_req(), session=store.get_session("s"), usage={}, final_text="Starting.Edited.",
        history=[], tool_calls=calls, _ordered_blocks=chosen, _agentic_tool_names=set(),
        _placeholder_inserted=True, cancel_event=stop, assistant_msg_id="u_reply", user_msg_id="u",
    )
    assert _owners(blocks) == _owners(chosen)
    assert blocks[1]["task_effect"] == {"task": "T1", "step": "1"}
    assert _owners(_stored(store)["blocks"]) == _owners(chosen)
