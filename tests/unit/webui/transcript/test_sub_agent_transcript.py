"""A sub-agent's turns stay out of the conversation's transcript.

Job turns run in their parent session, so the production driver stamps
each of their chat frames with ``job_id`` and the web client keeps them
with the turn's Sub-agents card. The card reads the agent's whole branch
(its first task and every later message to it) through
``load_sub_agent_transcript`` under its own view key, without moving the
session head.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from openprogram.runtime.conversation.production_driver.execution import ExecutionOperations


def _sink(event_sink, *, job: bool, session_id: str | None = None):
    owner = SimpleNamespace(event_sink=event_sink)
    attempt = SimpleNamespace(execution_id="j_abc")
    request = SimpleNamespace(_job_context=object() if job else None, session_id=session_id)
    return ExecutionOperations._turn_event_sink(owner, attempt, request)


def test_job_turn_frames_carry_the_job_id():
    frames = []
    emit = _sink(frames.append, job=True)
    emit({"type": "chat_response", "data": {"type": "stream_event", "session_id": "s1"}})
    emit({"type": "job_status", "data": {"job_id": "j_abc"}})
    emit({"type": "chat_response", "data": None})
    emit({"type": "chat_ack", "data": {"session_id": "s1", "msg_id": "j_abc_user"}})
    assert frames[0]["data"] == {"type": "stream_event", "session_id": "s1", "job_id": "j_abc"}
    assert frames[1] == {"type": "job_status", "data": {"job_id": "j_abc"}}
    assert frames[2] == {"type": "chat_response", "data": None}
    # The Job's ack names its own turn, so a client does not mark the
    # conversation busy with a turn that never sends it a clear.
    assert frames[3]["data"] == {"session_id": "s1", "msg_id": "j_abc_user", "job_id": "j_abc"}


def test_conversation_turn_frames_name_their_session():
    """A client that reloaded mid-turn has no ack to map the reply's msg_id;
    the frame itself must say which conversation it belongs to."""
    frames = []
    emit = _sink(frames.append, job=False, session_id="s1")
    emit({"type": "chat_response", "data": {"type": "stream_event", "msg_id": "m1"}})
    emit({"type": "chat_response", "data": {"type": "result", "msg_id": "m1", "session_id": "s1"}})
    emit({"type": "job_status", "data": {"job_id": "j"}})
    assert frames[0]["data"] == {"type": "stream_event", "msg_id": "m1", "session_id": "s1"}
    assert frames[1]["data"] == {"type": "result", "msg_id": "m1", "session_id": "s1"}
    assert frames[2] == {"type": "job_status", "data": {"job_id": "j"}}
    assert _sink(None, job=True) is None


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A conversation whose turn spawned one agent, then sent it a follow-up."""
    from openprogram.runtime.context import session_db as sdb_mod
    from openprogram.state.store.session.session_store import SessionStore

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")

    def add(mid, role, content, parent, ts):
        s.append_message("p1", {
            "id": mid, "role": role, "content": content,
            "timestamp": ts, "predecessor": parent,
        })

    add("u1", "user", "spawn one", None, 1)
    add("a1", "assistant", "spawned", "u1", 2)
    s.spawn_branch(
        "p1", "a1", source="agent", node_id="j_a_user", created_at=3,
        prompt="[task from p1:a1] This is a tracked job.\n\nfind it",
        register_head=False,
    )
    add("j_a_user_reply", "assistant", "found it", "j_a_user", 4)
    add("j_b_user", "user", "[message from p1:a1] To reply...\n\nand more?", "j_a_user_reply", 5)
    add("j_b_user_reply", "assistant", "here is more", "j_b_user", 6)
    s.commit_turn("p1", "init")
    return s


def test_branch_tip_follows_later_messages_to_the_agent(store):
    from openprogram.interfaces.server.transcript.turn_index import branch_tip

    messages = store.get_messages("p1")
    assert branch_tip(messages, "j_a_user_reply") == "j_b_user_reply"
    assert branch_tip(messages, "a1") == "a1"


def test_transcript_reads_the_agent_branch_in_turn_order(store):
    from openprogram.interfaces.server.ws_actions.sub_agent import _transcript

    tip, turns, window = _transcript("p1", "", "j_a_user_reply")
    assert tip == "j_b_user_reply"
    assert [[row["id"] for row in turn["rows"]] for turn in turns] == [
        ["j_a_user", "j_a_user_reply"], ["j_b_user", "j_b_user_reply"],
    ]
    assert (window["head_id"], window["start"], window["end"], window["total"]) == ("j_b_user_reply", 0, 2, 2)
    assert _transcript("p1", "", "missing") == ("missing", [], None)


def test_transcript_action_replies_without_moving_the_head(store):
    from openprogram.interfaces.server.ws_actions.sub_agent import ACTIONS

    sent = []

    class _Ws:
        async def send_text(self, text):
            sent.append(json.loads(text))

    head_before = store.get_session("p1").get("head_id")
    asyncio.run(ACTIONS["load_sub_agent_transcript"](_Ws(), {
        "session_id": "p1", "head_id": "j_a_user_reply", "request_id": "r1",
    }))
    asyncio.run(ACTIONS["load_sub_agent_transcript"](_Ws(), {"session_id": "p1"}))
    assert sent[0]["type"] == "sub_agent_transcript"
    assert sent[0]["data"]["request_id"] == "r1"
    assert sent[0]["data"]["head_id"] == "j_b_user_reply"
    assert sent[0]["data"]["view"] == "p1#sub-agent:j_a_user_reply"
    assert sum(len(turn["rows"]) for turn in sent[0]["data"]["turns"]) == 4
    assert sent[0]["data"]["window"]["head_id"] == "j_b_user_reply"
    assert "messages" not in sent[0]["data"]
    assert "error" in sent[1]["data"]
    assert store.get_session("p1").get("head_id") == head_before


def _read_rows(session_id, head_id):
    from openprogram.interfaces.server.ws_actions.sub_agent import _transcript

    _, turns, _ = _transcript(session_id, "", head_id)
    return {row["id"]: row for turn in turns for row in turn["rows"]}


def _append(store, mid, role, parent, ts, **fields):
    store.append_message("p1", {
        "id": mid, "role": role, "content": fields.pop("content", ""),
        "timestamp": ts, "predecessor": parent, **fields,
    })


def test_the_expanded_view_shows_tools_and_thinking_after_open(store):
    blocks = [
        {"type": "thinking", "text": "plan the search"},
        {"type": "tool", "tool": "read", "tool_call_id": "c1", "input": "{}", "result": "seen"},
        {"type": "text", "text": "found it"},
    ]
    store.update_node("p1", "j_a_user_reply", blocks=blocks, status="done")
    store.commit_turn("p1", "trace")

    reply = _read_rows("p1", "j_a_user_reply")["j_a_user_reply"]
    assert [block["type"] for block in reply["blocks"]] == ["thinking", "tool", "text"]
    assert reply["blocks"][1]["tool"] == "read"
    assert reply["blocks"][1]["result"] == "seen"
    assert reply["status"] == "done"


@pytest.mark.parametrize("stored, shown", [
    ("error", "error"), ("cancelled", "cancelled"), ("streaming", "streaming"),
])
def test_the_expanded_view_shows_how_a_turn_ended(store, stored, shown):
    store.update_node("p1", "j_b_user_reply", status=stored)
    store.commit_turn("p1", "ended")

    assert _read_rows("p1", "j_a_user_reply")["j_b_user_reply"]["status"] == shown


def test_a_later_turn_appears_in_the_view_already_read(store):
    assert "j_c_user" not in _read_rows("p1", "j_a_user_reply")
    _append(store, "j_c_user", "user", "j_b_user_reply", 7, content="third")
    _append(store, "j_c_user_reply", "assistant", "j_c_user", 8, content="third reply")
    store.commit_turn("p1", "more")

    rows = _read_rows("p1", "j_a_user_reply")
    assert list(rows)[-2:] == ["j_c_user", "j_c_user_reply"]


def test_a_sub_agent_read_stands_apart_from_the_conversation_view(store):
    """One session, two views: reading the agent neither changes nor enters the parent's."""
    from openprogram.interfaces.server.transcript import reader
    from openprogram.interfaces.server.transcript.turn_index import forget_views, get_view
    from openprogram.runtime.context.session_db import default_db

    forget_views()
    db = default_db()
    _read_rows("p1", "j_a_user_reply")
    turns, window = reader.first_read(db, get_view(db, "p1"))
    ids = [row["id"] for turn in turns for row in turn["rows"]]
    assert ids == ["u1", "a1"]
    assert window["head_id"] == "a1"
    assert all("found it" not in str(turn) and "here is more" not in str(turn) for turn in turns)
    assert store.get_session("p1").get("head_id") == "a1"


def _attach(store, card_id, caller, job_id, ts):
    store.append_message("p1", {
        "id": card_id, "role": "assistant", "display": "runtime", "function": "attach",
        "content": "", "predecessor": caller, "timestamp": ts,
        "extra": json.dumps({"attach": {"job_id": job_id, "label": job_id}}),
    }, advance_head=False)


def _inherit_turn(store, job_id, parent, ts):
    for mid, role, predecessor, at in (
        (f"{job_id}_user", "user", parent, ts),
        (f"{job_id}_user_reply", "assistant", f"{job_id}_user", ts + 1),
    ):
        store.append_message("p1", {
            "id": mid, "role": role, "content": mid, "timestamp": at, "predecessor": predecessor,
            "source": "agent_spawn",
        }, advance_head=False)


def _attached_jobs(row):
    return [card["attach"]["job_id"] for card in row.get("attachCards") or []]


def test_an_inheriting_agent_draws_no_step_row_on_the_replies_it_copied(store):
    """Its own card and its siblings' hang off the caller's reply, on its chain."""
    _inherit_turn(store, "j_i", "a1", 10)
    _attach(store, "own-card", "a1", "j_i", 12)
    _attach(store, "sibling-card", "a1", "j_s", 13)
    _attach(store, "child-card", "j_i_user_reply", "j_child", 14)
    store.commit_turn("p1", "inherit")

    rows = _read_rows("p1", "j_i_user_reply")
    assert list(rows) == ["u1", "a1", "j_i_user", "j_i_user_reply"]
    assert _attached_jobs(rows["a1"]) == []
    assert _attached_jobs(rows["j_i_user_reply"]) == ["j_child"]


def test_an_agent_inheriting_from_an_agent_starts_at_its_own_prompt(store):
    """Inherited rows are the caller's even when the caller is an agent too."""
    _inherit_turn(store, "j_i", "j_a_user_reply", 10)
    _attach(store, "own-card", "j_a_user_reply", "j_i", 12)
    store.commit_turn("p1", "nested")

    assert _attached_jobs(_read_rows("p1", "j_i_user_reply")["j_a_user_reply"]) == []


def test_a_later_turn_of_an_agent_keeps_the_step_rows_it_started(store):
    _attach(store, "first-child", "j_a_user_reply", "j_child1", 7)
    _attach(store, "later-child", "j_b_user_reply", "j_child2", 8)
    store.commit_turn("p1", "children")

    rows = _read_rows("p1", "j_a_user_reply")
    assert _attached_jobs(rows["j_a_user_reply"]) == ["j_child1"]
    assert _attached_jobs(rows["j_b_user_reply"]) == ["j_child2"]


def test_the_conversation_keeps_its_step_rows_when_an_agent_inherits(store):
    from openprogram.interfaces.server.transcript import reader
    from openprogram.interfaces.server.transcript.turn_index import forget_views, get_view
    from openprogram.runtime.context.session_db import default_db

    _inherit_turn(store, "j_i", "a1", 10)
    _attach(store, "own-card", "a1", "j_i", 12)
    store.commit_turn("p1", "inherit")
    forget_views()
    db = default_db()
    _read_rows("p1", "j_i_user_reply")
    turns, _ = reader.first_read(db, get_view(db, "p1"))
    by_id = {row["id"]: row for turn in turns for row in turn["rows"]}
    assert _attached_jobs(by_id["a1"]) == ["j_i"]


def test_a_finished_job_records_its_reply_as_branch_head():
    from openprogram.runtime.jobs.runner.shared import _turn_head_id
    from openprogram.runtime.conversation.turn_runtime.types import TurnResult

    class _Executions:
        def get_execution_input(self, job_id):
            return SimpleNamespace(assistant_message_id=f"{job_id}_user_reply")

    turn = TurnResult(final_text="", user_msg_id="j_a_user", assistant_msg_id="j_a_user_reply")
    assert _turn_head_id(_Executions(), "j_a", turn) == "j_a_user_reply"
    assert _turn_head_id(_Executions(), "j_a", SimpleNamespace(head_id="h1")) == "h1"
    # A turn that raised leaves no result; its admitted reply id stands in.
    assert _turn_head_id(_Executions(), "j_b", None) == "j_b_user_reply"
    assert _turn_head_id(SimpleNamespace(), "j_c", None) is None


def _spawn_rows():
    rows = [
        {"id": "u1", "role": "user", "predecessor": None, "timestamp": 1},
        {"id": "a1", "role": "assistant", "predecessor": "u1", "timestamp": 2},
        {"id": "card", "role": "assistant", "function": "attach", "caller": "a1",
         "predecessor": None, "attach": {"job_id": "j_a", "label": "研究", "head_id": None}},
        {"id": "u2", "role": "user", "predecessor": "a1", "timestamp": 5},
        {"id": "a2", "role": "assistant", "predecessor": "u2", "timestamp": 6},
        {"id": "j_a_user", "role": "user", "predecessor": "", "caller": "a1",
         "source": "agent_spawn", "timestamp": 3},
        {"id": "j_a_user_reply", "role": "assistant", "predecessor": "j_a_user",
         "source": "agent_spawn", "timestamp": 4},
    ]
    return rows


def test_a_sub_agent_branch_names_the_way_back_to_the_conversation():
    from openprogram.interfaces.server.ws_actions.session import _spawned_branch

    rows = _spawn_rows()
    assert _spawned_branch("j_a_user_reply", rows) == {
        "root_id": "j_a_user",
        "caller_id": "a1",
        # The newest turn below the spawning reply, not the reply itself.
        "back_target": "a2",
        "first_reply": "j_a_user_reply",
        "label": "研究",
    }
    assert _spawned_branch("a2", rows) is None


def test_the_way_back_passes_through_an_agent_notice(store):
    """A completion notice is a runtime user row; the conversation goes on
    through it, so neither the way back nor the branch list stops there."""
    from openprogram.interfaces.server.ws_actions.session import _spawned_branch

    rows = _spawn_rows() + [
        {"id": "n1", "role": "user", "predecessor": "a2", "display": "runtime",
         "source": "job_followup", "timestamp": 7},
        {"id": "a3", "role": "assistant", "predecessor": "n1",
         "source": "job_followup", "timestamp": 8},
    ]
    assert _spawned_branch("j_a_user_reply", rows)["back_target"] == "a3"

    store.append_message("p1", {"id": "n1", "role": "user", "content": "notice",
                                "timestamp": 7, "predecessor": "a1",
                                "display": "runtime", "source": "job_followup"})
    store.append_message("p1", {"id": "a3", "role": "assistant", "content": "ok",
                                "timestamp": 8, "predecessor": "n1"})
    store.commit_turn("p1", "notice")
    tips = {b["head_msg_id"] for b in store.list_branches("p1")}
    assert "a3" in tips and "a1" not in tips


def test_a_branch_stopped_before_its_card_is_named_by_its_agent_call():
    import json

    from openprogram.interfaces.server.ws_actions.session import _spawned_branch

    rows = [row for row in _spawn_rows() if row["id"] != "card"]
    rows[-2]["content"] = "Check the close path"
    for prompt, description in (("Other work", "Write docs"), ("Check the close path", "Fix page close")):
        rows.append({
            "id": f"call-{description}", "role": "tool", "function": "agent", "caller": "a1",
            "extra": json.dumps({"tool_use": {"name": "agent", "arguments": json.dumps(
                {"prompt": prompt, "description": description})}}),
        })
    assert _spawned_branch("j_a_user_reply", rows)["label"] == "Fix page close"


def test_a_job_branch_root_without_a_pointer_head_gets_its_spawn_origin():
    from openprogram.interfaces.server.ws_actions.session import _annotate_spawn_origin

    rows = _spawn_rows()
    graph = [dict(row) for row in rows if row.get("function") != "attach"]
    _annotate_spawn_origin(graph, rows)
    by_id = {row["id"]: row for row in graph}
    assert by_id["j_a_user"]["spawned_from"] == {"caller_id": "a1", "label": "研究"}
    assert "spawned_from" not in by_id["u2"]
