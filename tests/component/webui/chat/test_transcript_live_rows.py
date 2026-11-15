"""Open replies, trees and the socket filter as the publisher sends them."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from openprogram.interfaces.server import ws_delivery
from openprogram.interfaces.server.transcript import publisher, turn_index
from openprogram.runtime.conversation.run_control.turns import begin_turn, end_turn
from openprogram.state.store.session import session_log
from tests.support.transcript.live_server import RecordingSocket, live_chat
from tests.support.transcript.old_shapes import Rows


@pytest.fixture
def chat(tmp_path: Path):
    with live_chat(tmp_path) as live:
        rows = Rows(live)
        rows.turn("a")
        rows.finish()
        live.rows = rows
        live.client_read("open")
        live.wait_published()
        live.timeline.clear()
        yield live


def _changes(chat) -> list[dict]:
    chat.wait_published()
    return [
        change for step in chat.timeline if step.get("frame", {}).get("type") == "transcript_update"
        for change in step["frame"]["data"]["changes"]
    ]


@pytest.mark.parametrize("fields", [
    {"chapter": {"pinned_at": 5.0}}, {"reverted": True}, {"usage": {"input": 3}}, {"output": "rewritten by an effect"},
])
def test_a_change_to_a_finished_row_is_one_row_upsert(chat, fields):
    chat.sessions.update_node(chat.session_id, "a_reply", **fields)
    changes = [c for c in _changes(chat) if c.get("row", {}).get("id") == "a_reply" or c.get("rowId") == "a_reply"]
    assert [c["kind"] for c in changes] == ["row_upsert"]


def test_a_paused_run_draws_paused_then_running_and_a_read_agrees(chat):
    _, reply = _open_reply(chat)
    chat.sessions.update_node(chat.session_id, reply, status="paused", pause_reason="system_access")
    assert [c["fields"]["status"] for c in _changes(chat) if c["kind"] == "row_patch"] == ["paused"]
    drawn = next(row for frame in chat.whole_read() for turn in frame["data"].get("turns", []) for row in turn["rows"] if row["id"] == reply)
    assert drawn["status"] == "paused"
    chat.timeline.clear()
    chat.sessions.update_node(chat.session_id, reply, status="running", pause_reason=None)
    assert [c["fields"]["status"] for c in _changes(chat) if c["kind"] == "row_patch"] == ["running"]


def test_a_second_message_to_an_expanded_agent_appears_in_its_view(chat):
    rows = chat.rows
    rows.add("job-user", "user", "do it", chain=False, predecessor="a_reply")
    rows.add("job-reply", "assistant", "done", chain=False, predecessor="job-user")
    view_key = turn_index.sub_agent_view_key(chat.session_id, "job")
    turn_index.get_sub_agent_view(chat.sessions, chat.session_id, "job", "job-reply")
    socket = RecordingSocket(lambda _frame: None, live=chat._live_frame)
    publisher.current().hold(socket, chat.session_id, view_key)
    chat.timeline.clear()
    rows.add("job-user-2", "user", "and this too", chain=False, predecessor="job-reply")
    chat.wait_published()
    frames = [s["frame"]["data"] for s in chat.timeline if s.get("frame", {}).get("type") == "transcript_update"]
    assert any(f["view"] == view_key and c["kind"] == "turn_upsert" and c["turn"]["id"] == "job-user-2"
               for f in frames for c in f["changes"])
    publisher.current().release(socket)


def _open_reply(chat, name: str = "b") -> tuple[str, str]:
    """A new turn whose reply is still streaming, with what it sent cleared."""
    rows = chat.rows
    user = rows.add(f"{name}-user", "user", "question")
    reply = rows.add(f"{name}-reply", "assistant", "", status="streaming", extra={"blocks": []})
    rows.finish(reply)
    chat.wait_published()
    chat.timeline.clear()
    return user, reply


def test_a_user_row_and_its_open_reply_are_published(chat):
    rows = chat.rows
    user = rows.add("new-user", "user", "question", client_request_id="req-1")
    reply = rows.add("new-reply", "assistant", "", status="streaming")
    rows.finish(reply)
    changes = _changes(chat)
    opener = next(c for c in changes if c["kind"] == "turn_upsert")
    assert opener["turn"]["rows"][0]["id"] == user
    assert opener["turn"]["rows"][0]["clientRequestId"] == "req-1"
    assert any(c.get("row", {}).get("id") == reply for c in changes if c["kind"] == "row_upsert") or reply in {
        row["id"] for row in opener["turn"]["rows"]
    }


def test_a_tool_block_goes_as_one_item_and_only_when_it_changed(chat):
    _, reply = _open_reply(chat)
    block = {"type": "tool", "tool": "bash", "tool_call_id": "c1", "input": "ls", "result": None, "is_error": False}
    db = chat.sessions
    db.update_node(chat.session_id, reply, extra={"blocks": [block], "stream_seq": 1})
    items = [c for c in _changes(chat) if c["kind"] == "item_upsert" and c["list"] == "blocks"]
    assert [item["index"] for item in items] == [0]
    chat.timeline.clear()
    db.update_node(chat.session_id, reply, extra={"blocks": [block], "stream_seq": 2})
    assert not [c for c in _changes(chat) if c["kind"] == "item_upsert" and c["list"] == "blocks"]


def test_an_open_reply_never_carries_its_text_in_a_patch(chat):
    _, reply = _open_reply(chat)
    chat.sessions.update_node(chat.session_id, reply, usage={"input": 3}, output="partial")
    patches = [c for c in _changes(chat) if c["kind"] == "row_patch" and c["rowId"] == reply]
    assert patches and all("content" not in patch["fields"] and "blocks" not in patch["fields"] for patch in patches)


def test_the_terminal_reply_goes_whole_as_one_row(chat):
    _, reply = _open_reply(chat)
    chat.sessions.update_node(chat.session_id, reply, status="done", output="the answer")
    upserts = [c for c in _changes(chat) if c["kind"] == "row_upsert" and c["row"]["id"] == reply]
    assert len(upserts) == 1 and upserts[0]["row"]["content"] == "the answer" and upserts[0]["row"]["status"] == "done"


def _publish_one_drain(chat, write) -> None:
    """Everything ``write`` does reaches the publisher in a single drain."""
    live = publisher.current()
    collector = publisher.TranscriptPublisher(live._send)
    collector._sessions[chat.session_id] = {chat.session_id}
    session_log.set_record_observer(collector.notice)
    try:
        write(collector)
    finally:
        session_log.set_record_observer(live.notice)
    collector._wait_and_drain()
    live._publish_session(chat.session_id, collector._dirty.pop(chat.session_id))


@contextmanager
def _stopped_turn(chat, user: str, reply: str):
    """A registered execution anchored to these rows, with Stop already asked of it."""
    revision = chat.store.create_revision(manifest={"entrypoint": "test"})
    execution = chat.store.admit_execution(
        session_id=chat.session_id, revision_id=revision.revision_id, input_ref="input", input_hash="hash",
        entrypoint="test", trusted_actor={"subject": "test"}, config_snapshot_ref="config",
        user_message_id=user, assistant_message_id=reply,
    )
    token = begin_turn(chat.session_id, execution.execution_id)
    token.cancel()
    try:
        yield
    finally:
        end_turn(chat.session_id, token)


def test_nothing_but_the_terminal_row_goes_out_after_stop(chat):
    user, reply = _open_reply(chat)
    with _stopped_turn(chat, user, reply):
        chat.sessions.update_node(chat.session_id, reply, usage={"input": 3})
        assert not _changes(chat)
        chat.sessions.update_node(chat.session_id, reply, status="cancelled")
        assert [c["row"]["status"] for c in _changes(chat) if c["kind"] == "row_upsert"] == ["cancelled"]


def test_a_running_tree_does_not_reach_a_reply_being_stopped(chat):
    user, reply = _open_reply(chat)
    with _stopped_turn(chat, user, reply):
        publisher.current().notice_tree(chat.session_id, reply, {"path": "p1", "name": "fn", "status": "running"}, "fn")
        assert not _changes(chat)


def test_a_steer_whose_reparent_comes_in_a_later_tick_is_drawn_and_then_placed(chat):
    user, reply = _open_reply(chat)
    chat.rows.add("steer", "user", "change course", chain=False, predecessor=user, steering=True)
    first = next(c for c in _changes(chat) if c["kind"] == "row_upsert" and c["row"]["id"] == "steer")
    assert first["afterRowId"] == user and "steeringReplyId" not in first["row"]
    chat.timeline.clear()
    chat.sessions.update_node(chat.session_id, reply, predecessor="steer")
    placed = next(c for c in _changes(chat) if c["kind"] == "row_upsert" and c["row"]["id"] == "steer")
    assert placed["row"]["steeringReplyId"] == reply and placed["afterRowId"] == user


def test_a_steer_and_its_reparent_in_one_drain_go_out_placed_and_once(chat):
    user, reply = _open_reply(chat)

    def write(_collector):
        chat.rows.add("steer", "user", "change course", chain=False, predecessor=user, steering=True)
        chat.sessions.update_node(chat.session_id, reply, predecessor="steer")

    _publish_one_drain(chat, write)
    sent = [c for c in _changes(chat) if c["kind"] == "row_upsert" and c["row"]["id"] == "steer"]
    assert len(sent) == 1 and sent[0]["row"]["steeringReplyId"] == reply


def test_a_steer_that_never_gets_its_reparent_is_sent_once(chat):
    user, _reply = _open_reply(chat)
    chat.rows.add("steer", "user", "change course", chain=False, predecessor=user, steering=True)
    assert len([c for c in _changes(chat) if c.get("row", {}).get("id") == "steer"]) == 1
    chat.timeline.clear()
    chat.sessions.update_node(chat.session_id, "a_reply", usage={"input": 1})
    assert not [c for c in _changes(chat) if c.get("row", {}).get("id") == "steer"]


def test_trees_of_two_call_roots_in_one_drain_both_go_out(chat):
    _, reply = _open_reply(chat)

    def write(collector):
        collector.notice_tree(chat.session_id, reply, {"path": "p1", "name": "fn", "status": "completed", "children": [{"path": "k1"}]}, "fn")
        collector.notice_tree(chat.session_id, reply, {"path": "p2", "name": "fn", "status": "running", "children": [{"path": "k2"}]}, "fn")

    _publish_one_drain(chat, write)
    roots = {c["item"]["path"]: c["item"]["status"] for c in _changes(chat) if c.get("list") == "callRoots"}
    assert roots == {"p1": "completed", "p2": "running"}


def test_a_field_cleared_on_an_open_reply_goes_out_as_null(chat):
    _, reply = _open_reply(chat)
    chat.sessions.update_node(chat.session_id, reply, error_detail="boom")
    assert any(c["fields"].get("errorDetail") == "boom" for c in _changes(chat) if c["kind"] == "row_patch")
    chat.timeline.clear()
    chat.sessions.update_node(chat.session_id, reply, error_detail=None)
    patches = [c for c in _changes(chat) if c["kind"] == "row_patch" and c["rowId"] == reply]
    assert patches and patches[-1]["fields"]["errorDetail"] is None


def test_a_field_the_whole_row_carried_is_cleared_too(chat):
    rows = chat.rows
    rows.add("c-user", "user", "question")
    reply = rows.add("c-reply", "assistant", "", status="streaming", error_detail="boom")
    rows.finish(reply)
    chat.wait_published()
    chat.timeline.clear()
    chat.sessions.update_node(chat.session_id, reply, error_detail=None)
    patches = [c for c in _changes(chat) if c["kind"] == "row_patch" and c["rowId"] == reply]
    assert patches and patches[-1]["fields"] == {"errorDetail": None}


def test_a_function_card_settles_on_its_terminal_tree_while_the_row_is_still_open(chat):
    _, reply = _open_reply(chat)
    kids = [{"path": "k1"}]
    publisher.current().notice_tree(chat.session_id, reply, {"path": "p1", "name": "fn", "status": "running", "children": kids}, "fn")
    publisher.current().notice_tree(chat.session_id, reply, {"path": "p1", "name": "fn", "status": "completed", "children": kids}, "fn")
    roots = [c for c in _changes(chat) if c["kind"] == "item_upsert" and c["list"] == "callRoots"]
    assert roots[-1]["item"]["status"] == "completed" and roots[-1]["rowId"] == reply


def test_a_flat_call_sends_no_tree_because_a_read_draws_none(chat):
    _, reply = _open_reply(chat)
    publisher.current().notice_tree(chat.session_id, reply, {"path": "p1", "name": "fn", "status": "running"}, "fn")
    assert not [c for c in _changes(chat) if c.get("list") == "callRoots"]


def test_a_function_run_row_takes_tree_and_status_from_the_root(chat):
    rows = chat.rows
    rows.add("user-f", "user", "/run fn")
    run = rows.add("run-f", "assistant", "", status="running", display="runtime", function="fn")
    rows.finish(run)
    chat.wait_published()
    chat.timeline.clear()
    publisher.current().notice_tree(chat.session_id, run, {"path": run, "status": "error"}, "fn")
    patch = next(c for c in _changes(chat) if c["kind"] == "row_patch" and c["rowId"] == run)
    assert patch["fields"]["status"] == "error" and patch["fields"]["contextTree"]["path"] == run


@pytest.mark.parametrize("frame,dropped", [
    ({"type": "chat_response", "data": {"type": "tree_update", "session_id": "s"}}, True),
    ({"type": "chat_response", "data": {"type": "user_message", "session_id": "s"}}, True),
    ({"type": "chat_response", "data": {"type": "stream_event", "event": {"type": "tool_use"}}}, True),
    ({"type": "chat_response", "data": {"type": "stream_event", "event": {"type": "text"}}}, False),
    ({"type": "chat_response", "data": {"type": "result", "session_id": "s"}}, False),
])
def test_a_version_3_socket_gets_only_frames_that_write_no_row_twice(frame, dropped):
    socket = RecordingSocket(lambda _frame: None)
    socket._history_protocol = 3
    assert ws_delivery.frame_is_for_older_clients(socket, json.dumps(frame)) is dropped
