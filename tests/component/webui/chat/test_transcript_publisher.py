"""What the store's writes become on a socket holding a transcript view, on the real emit path."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from openprogram.interfaces.server.transcript import publisher, turn_index
from openprogram.interfaces.server.ws_actions import merge as merge_actions
from openprogram.operations.self_update.delivery import owner_notice
from openprogram.runtime.conversation.merge import MergeTurnResult
from openprogram.state.store.session import session_log
from tests.support.transcript.chat_scenarios import sync_sub_agent
from tests.support.transcript.live_server import RecordingSocket, live_chat
from tests.support.transcript.old_shapes import Rows


@pytest.fixture
def chat(tmp_path: Path):
    with live_chat(tmp_path) as live:
        yield live


def _updates(chat) -> list[dict]:
    return [step["frame"]["data"] for step in chat.timeline if step.get("frame", {}).get("type") == "transcript_update"]


def _changes(chat) -> list[dict]:
    return [change for update in _updates(chat) for change in update["changes"]]


def _opened(chat) -> Rows:
    rows = Rows(chat)
    rows.turn("a")
    rows.finish()
    chat.client_read("open")
    chat.wait_published()
    chat.timeline.clear()
    return rows


def test_a_spawn_card_arrives_as_an_item_at_its_reply_and_settles(chat) -> None:
    rows = _opened(chat)
    rows.add("card", "assistant", "agent", chain=False, predecessor="a_reply", display="runtime", function="attach",
             attach={"session_id": chat.session_id, "status": "running", "job_id": "j1"})
    chat.wait_published()
    chat.sessions.update_node(chat.session_id, "card", attach={"session_id": chat.session_id, "status": "completed", "job_id": "j1"})
    chat.wait_published()
    updates = _updates(chat)
    assert [change["kind"] for update in updates for change in update["changes"]] == ["item_upsert", "item_upsert"]
    first, second = (update["changes"][0] for update in updates)
    assert first["rowId"] == "a_reply" and first["list"] == "attachCards" and first["item"]["id"] == "card"
    assert second["item"]["attach"]["status"] == "completed"
    assert updates[1]["previous"] == updates[0]["revision"]
    assert not any(change["kind"] == "window_stale" for change in _changes(chat))


def test_a_sync_spawn_card_appears_settles_and_survives_a_reload(chat) -> None:
    chat.client_read("open")
    chat.wait_published()
    sync_sub_agent(chat)
    cards = [change for change in _changes(chat) if change["kind"] == "item_upsert" and change["list"] == "attachCards"]
    assert cards and cards[0]["item"]["attach"]["status"] == "running"
    assert cards[-1]["item"]["attach"]["status"] == "completed"
    assert not any(change["kind"] == "window_stale" for change in _changes(chat))
    loaded = next(frame["data"] for frame in chat.read() if frame["type"] == "session_loaded")
    reply = next(row for row in loaded["turns"][0]["rows"] if row["id"] == cards[-1]["rowId"])
    assert [card["id"] for card in reply["attachCards"]] == [cards[-1]["item"]["id"]]


def test_a_top_level_card_is_a_row_at_its_position(chat) -> None:
    rows = _opened(chat)
    rows.add("card", "assistant", "agent", chain=False, predecessor="a", display="runtime", function="attach",
             attach={"session_id": chat.session_id, "status": "running"})
    chat.wait_published()
    change = _changes(chat)[0]
    assert change["kind"] == "row_upsert" and change["row"]["id"] == "card" and change["afterRowId"] == "a"


def test_a_report_opener_is_a_new_turn(chat) -> None:
    rows = _opened(chat)
    rows.add("report", "assistant", "a finding", status="done", always_on_report="r1")
    rows.finish()
    chat.wait_published()
    change = _changes(chat)[0]
    assert change["kind"] == "turn_upsert" and change["turn"]["id"] == "report" and change["afterTurnId"] == "a"


def test_a_system_row_joins_its_turn(chat) -> None:
    rows = _opened(chat)
    rows.add("note", "system", "a notice", predecessor="a_reply")
    rows.finish()
    chat.wait_published()
    kinds = {change["kind"] for change in _changes(chat)}
    assert kinds == {"turn_upsert"} or kinds == {"row_upsert"}, "a system row is delivered once"


def test_a_settled_rows_change_is_a_row_upsert_at_the_same_place(chat) -> None:
    _opened(chat)
    chat.sessions.update_node(chat.session_id, "a", chapter={"pinned_at": 5.0})
    chat.wait_published()
    change = _changes(chat)[0]
    assert change["kind"] == "row_upsert" and change["row"]["id"] == "a" and change["afterRowId"] is None
    assert change["row"]["chapterPinnedAt"]


def test_a_deleted_node_and_a_branch_move_make_the_window_stale(chat) -> None:
    rows = _opened(chat)
    rows.turn("b")
    rows.finish()
    chat.client_read("again")
    chat.wait_published()
    chat.timeline.clear()
    chat.sessions.set_head(chat.session_id, "a_reply")
    chat.wait_published()
    chat.sessions.delete_nodes(chat.session_id, ["b_reply"])
    chat.wait_published()
    assert [change["reason"] for change in _changes(chat) if change["kind"] == "window_stale"] == ["branch", "structure"]


def _first_row_of_read(chat) -> dict:
    """The first row of the newest read in the timeline."""
    read = next(step["read"] for step in reversed(chat.timeline) if "read" in step)
    loaded = next(frame["data"] for frame in read["frames"] if frame["type"] == "session_loaded")
    return loaded["turns"][0]["rows"][0]


def test_a_turn_on_a_branch_that_is_not_shown_moves_the_arrow_that_leads_to_it(chat) -> None:
    rows = Rows(chat)
    rows.turn("b")
    rows.last = "ROOT"
    rows.turn("a")
    rows.finish()
    chat.client_read("open")
    chat.wait_published()
    assert _first_row_of_read(chat)["prevSiblingId"] == "b_reply"
    chat.timeline.clear()
    # The other branch's turn: its rows hang from that branch's tip and the head stays.
    rows.add("b2", "user", "b2 prompt", chain=False, predecessor="b_reply")
    rows.add("b2_reply", "assistant", "the other branch's reply", chain=False, predecessor="b2", status="done")
    chat.wait_published()
    assert chat.sessions.get_session(chat.session_id)["head_id"] == "a_reply"
    assert not any(change["kind"] in ("window_stale", "turn_upsert", "row_upsert") for change in _changes(chat))
    arrow = [change for change in _changes(chat) if change["kind"] == "row_patch" and change["rowId"] == "a"]
    assert arrow and arrow[-1]["turnId"] == "a" and arrow[-1]["fields"] == {"prevSiblingId": "b2_reply"}
    chat.client_read("again")
    chat.wait_published()
    assert _first_row_of_read(chat)["prevSiblingId"] == "b2_reply", "the patch says what a read says"
    # A row on the shown branch moves no arrow.
    chat.timeline.clear()
    rows.turn("a2")
    rows.finish()
    chat.wait_published()
    assert not any(change["kind"] == "row_patch" and "prevSiblingId" in change["fields"] for change in _changes(chat))


def test_a_read_that_already_shows_the_moved_arrow_does_not_hide_the_move(chat, monkeypatch: pytest.MonkeyPatch) -> None:
    """The step that follows another branch's write runs after a second read
    has rebuilt the view; the holder that read first still gets the patch."""
    rows = Rows(chat)
    rows.turn("b")
    rows.last = "ROOT"
    rows.turn("a")
    rows.finish()
    chat.wait_published()
    chat.client_read("open")
    assert _first_row_of_read(chat)["prevSiblingId"] == "b_reply"
    chat.timeline.clear()
    live = publisher.current()
    second_read_done = threading.Event()
    publish_session = live._publish_session

    def after_the_second_read(session_id, dirty):
        assert second_read_done.wait(10)
        return publish_session(session_id, dirty)

    monkeypatch.setattr(live, "_publish_session", after_the_second_read)
    rows.add("b2", "user", "b2 prompt", chain=False, predecessor="b_reply")
    chat.client_read("another socket's read")
    assert _first_row_of_read(chat)["prevSiblingId"] == "b2"
    second_read_done.set()
    chat.wait_published()
    arrow = [change for change in _changes(chat) if change["kind"] == "row_patch" and change["rowId"] == "a"]
    assert arrow and arrow[-1]["fields"] == {"prevSiblingId": "b2"}


def test_the_dag_panel_is_told_about_an_attach(chat) -> None:
    rows = _opened(chat)
    sent: list[str] = []
    socket = RecordingSocket(lambda _frame: None, live=sent.append)
    publisher.hold(socket, chat.session_id)
    rows.add("card", "assistant", "agent", chain=False, predecessor="a_reply", display="runtime", function="attach",
             attach={"session_id": chat.session_id, "status": "running"})
    chat.wait_published()
    assert any(json.loads(item)["type"] == "branches_changed" for item in sent)


def test_a_view_rebuilt_while_publishing_counts_on_from_the_number_its_holders_hold(chat) -> None:
    """The rebuild's own update starts at the holders' cursor, so it applies
    without a repair read; every later update reports a number nobody has seen.
    Each write is published on its own, so the two never share one update."""
    rows = _opened(chat)
    held = turn_index.cached_view(chat.session_id)
    cursor = held.revision
    rows.add("event", "system", "an event", kind="event")
    chat.wait_published()
    assert turn_index.cached_view(chat.session_id) is not held
    rebuilt = _updates(chat)[-1]
    assert rebuilt["previous"] == cursor and rebuilt["revision"] > cursor
    rows.add("card", "assistant", "agent", chain=False, predecessor="a_reply", display="runtime", function="attach",
             attach={"session_id": chat.session_id, "status": "running"})
    chat.wait_published()
    update = _updates(chat)[-1]
    assert update["previous"] == rebuilt["revision"] != cursor and update["revision"] > update["previous"]
    assert turn_index.cached_view(chat.session_id).revision == update["revision"]


def test_a_view_a_socket_holds_is_not_evicted_and_the_cap_closes_the_oldest(chat) -> None:
    chat.read()
    sent: list[str] = []
    socket = RecordingSocket(lambda _frame: None, live=sent.append)
    for number in range(turn_index.MAX_VIEWS + 1):
        publisher.hold(socket, f"session-{number}")
    assert [json.loads(item) for item in sent] == [{"type": "view_closed", "data": {"session_id": "session-0", "view": "session-0"}}]
    assert turn_index.cached_view(chat.session_id) is not None


def test_a_view_nobody_holds_is_dropped_from_the_lru_past_the_cap(chat) -> None:
    for number in range(turn_index.MAX_VIEWS + 4):
        turn_index._views[f"loose-{number}"] = object()  # type: ignore[assignment]
    turn_index.pin_view("loose-0")
    with turn_index._registry_lock:
        turn_index._evict_views()
    assert "loose-0" in turn_index._views and len(turn_index._views) <= turn_index.MAX_VIEWS + 1
    turn_index.unpin_view("loose-0")


def test_the_observer_only_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    """It runs inside the writer's append, so it may not read, project or wait."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("the observer touched a file")

    monkeypatch.setattr("builtins.open", refuse)
    instance = publisher.TranscriptPublisher(lambda *_: True)
    instance.notice("s", "node.put", ("n",), frozenset({"content"}))
    assert instance._queue.qsize() == 1


def test_a_forked_child_never_fires_the_observer(chat, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    session_log.set_record_observer(lambda *args: seen.append(args))
    monkeypatch.setattr(session_log.os, "getpid", lambda: -1)
    chat.sessions.append_message(chat.session_id, {"id": "x", "role": "user", "content": "x", "timestamp": time.time()})
    assert seen == []


def test_a_server_without_the_worker_lock_still_publishes(chat, monkeypatch: pytest.MonkeyPatch) -> None:
    """``python -m openprogram.interfaces.server`` never takes the lock, and its reloads are dropped all the same."""
    rows = _opened(chat)
    monkeypatch.setattr(session_log, "is_main_writer", lambda: False)
    rows.add("card", "assistant", "agent", chain=False, predecessor="a_reply", display="runtime", function="attach",
             attach={"session_id": chat.session_id, "status": "running"})
    chat.wait_published()
    assert [change["kind"] for change in _changes(chat)] == ["item_upsert"]


def test_a_streaming_write_does_not_reach_the_observer(chat, tmp_path: Path) -> None:
    seen: list = []
    log = session_log.SessionLog(tmp_path / "s")
    session_log.set_record_observer(lambda *args: seen.append(args[1]))
    log.write_history(0, "user", "n", {"id": "n", "role": "user"})
    log.patch_node("n", {"content": "more"}, quiet=True)
    assert seen == ["node.put"]


def test_a_card_written_once_and_read_again_has_the_same_position(chat) -> None:
    rows = _opened(chat)
    rows.add("card", "assistant", "agent", chain=False, predecessor="a_reply", display="runtime", function="attach",
             attach={"session_id": chat.session_id, "status": "running"})
    chat.wait_published()
    reply = chat.read()
    loaded = next(frame["data"] for frame in reply if frame["type"] == "session_loaded")
    cards = [card["id"] for row in loaded["turns"][0]["rows"] for card in row.get("attachCards") or []]
    assert cards == ["card"]


@pytest.fixture
def run_active(chat):
    """Undone before the chat's teardown, which waits for the run slot to clear."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("openprogram.interfaces.server.server._is_run_active", lambda _session_id: True)
        yield


def _notice_blocks(change: dict) -> list[str]:
    return [block["tool"] for block in change["row"].get("blocks") or []]


def test_a_notice_written_during_a_run_reaches_its_settled_reply(chat, run_active) -> None:
    _opened(chat)
    owner_notice._append(chat.sessions, chat.session_id, "n1", "the notice")
    chat.wait_published()
    change = _changes(chat)[0]
    assert change["kind"] == "row_upsert" and change["row"]["id"] == "a_reply"
    assert _notice_blocks(change) == ["self_update_notice"]


def test_a_report_posted_during_a_run_is_a_new_turn(chat, run_active) -> None:
    rows = _opened(chat)
    rows.add("report", "assistant", "a finding", status="done", always_on_report="r1")
    rows.finish()
    chat.wait_published()
    change = _changes(chat)[0]
    assert change["kind"] == "turn_upsert" and change["turn"]["id"] == "report"


def _merge(chat, monkeypatch: pytest.MonkeyPatch, rows: Rows) -> None:
    def process_merge_turn(target_session_id: str, **_arguments) -> MergeTurnResult:
        """The writes of a merge: pointers off the head, then a turn that moves it forward."""
        chat.sessions.append_message(target_session_id, {
            "id": "pointer", "role": "assistant", "content": "peer", "predecessor": "a_reply", "timestamp": time.time(),
            "display": "runtime", "function": "attach", "attach": {"session_id": target_session_id, "merge_temp": True},
        }, advance_head=False)
        rows.add("merge", "user", "merge prompt")
        rows.add("merge_reply", "assistant", "merged", status="done")
        rows.finish()
        return MergeTurnResult(target_session_id=target_session_id, target_assistant_id="merge_reply")

    monkeypatch.setattr("openprogram.runtime.conversation.merge.process_merge_turn", process_merge_turn)
    socket = RecordingSocket(lambda _frame: None)
    asyncio.run(merge_actions.handle_merge_branches(socket, {
        "session_id": chat.session_id, "peers": [{"session_id": chat.session_id, "head_id": "a_reply"}],
    }))
    chat.wait_published()


def test_a_successful_merge_sends_every_holder_to_read_at_latest(chat, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _opened(chat)
    _merge(chat, monkeypatch, rows)
    stale = [change for change in _changes(chat) if change["kind"] == "window_stale"]
    assert stale and stale[-1]["reason"] == "branch" and stale[-1]["head_id"] == "merge_reply"
    assert "reload" not in [step.get("read", {}).get("name") for step in chat.timeline]
