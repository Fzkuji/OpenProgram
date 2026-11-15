"""The committed chat recordings and old shapes match what the server does now."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.support.transcript import live_server
from tests.support.transcript.chat_scenarios import SCENARIOS
from tests.support.transcript.live_server import RecordingSocket, live_chat
from tests.support.transcript.old_shapes import OLD_SHAPES, Rows, build_case
from tests.support.transcript.record_chats import RECORDINGS, dump, record

DESIGN_SHAPES = {
    "attach_predecessor_only", "attach_both_edges", "attach_on_user_row", "attach_manual", "attach_cross_session",
    "reply_without_blocks", "row_without_status", "error_in_content", "row_without_id", "extra_as_object",
    "extra_as_json_string", "read_speaker", "read_notice", "pasted_elements", "chapter_pin", "gui_agent_name",
    "attach_no_call_id_prompt_match", "attach_no_call_id_order", "attach_no_call_id_order_skips_send_message",
    "attach_task_block", "attach_with_send_message", "attach_unmatched", "code_node_without_call_id",
    "receipt_without_origin", "old_steer_row", "steering_already_in_blocks", "restart_prompt", "fallback_blocks",
    "summary_covers_ids", "old_tool_row_by_provider_id", "head_folded_by_aggregation", "broken_predecessor_chain",
    "running_attach_without_job",
}
# Frame kinds whose number and order depend on thread timing, not on the code.
TIMING_ONLY = {"execution.updated", "execution.command.updated", "running_task", "running_task_clear",
               "job_status", "branches_list", "session_updated"}
# Numbers this large are clock readings: timestamps and run sequence numbers.
CLOCK_READING = 1_000_000_000
GENERATED_ID = re.compile(r"(?<![0-9a-zA-Z])[0-9a-f]{12,32}(?![0-9a-zA-Z])")
# How long a stopped call ran (``stop_reasons``): a clock reading too.
STOPPED_AFTER = re.compile(r"(?<=\bafter )\d+\.\d+s\b")
# How long a sub-agent ran, on its result block's usage line: a clock reading
# too, "0 s" on an idle machine and "1 s" on a loaded one.
AGENT_RAN_FOR = re.compile(r"(?<=\busage: )\d+(?:\.\d+)? (?:s|min|h)\b")
READ_REPLIES = {"session_loaded", "session_history_page"}
# The parts of a read the client draws from; the graph's order is a race.
READ_FIELDS = ("turns", "window", "head_id", "run_active")


def client_steps(timeline: list[dict]) -> list[str]:
    """What the recording's client does; how many reloads a stop sends is a race."""
    return [next(iter(step)) for step in timeline
            if "frame" not in step and (step.get("read") or {}).get("name") != "reload"]


def frame_kinds(timeline: list[dict]) -> set[tuple]:
    kinds = set()
    for step in timeline:
        frame = step.get("frame")
        if frame is None or frame["type"] in TIMING_ONLY:
            continue
        data = frame.get("data") or {}
        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        kinds.add((frame["type"], data.get("type"), event.get("type"), tuple(sorted(data))))
    return kinds


# Set by the machine or by earlier tests in the process, and read by no transcript code.
UNRECORDED_FIELDS = {"clientRequestId", "client_request_id", "worker_id", "revision", "memory_policy"}
# How long an agent ran (``agent_result`` usage on its attach card): a clock reading.
ELAPSED_FIELDS = {"duration_s"}


def _comparable(value, ids: dict[str, str]):
    """``value`` with clock readings and generated ids replaced by stable stand-ins."""
    if isinstance(value, dict):
        return {key: json.dumps(_comparable(json.loads(item), ids), sort_keys=True)
                if key == "extra" and isinstance(item, str) and item
                else "<seconds>" if key in ELAPSED_FIELDS and isinstance(item, (int, float))
                else _comparable(item, ids)
                for key, item in value.items() if key not in UNRECORDED_FIELDS}
    if isinstance(value, list):
        return [_comparable(item, ids) for item in value]
    if isinstance(value, str):
        value = AGENT_RAN_FOR.sub("<seconds>", STOPPED_AFTER.sub("<seconds>", value))
        return GENERATED_ID.sub(lambda match: ids.setdefault(match.group(0), f"<id {len(ids)}>"), value)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > CLOCK_READING:
        return "<clock>"
    return value


def stored_reads(recording: dict) -> list[dict]:
    """Every reply a read got and every cold transcript, in the recording's order."""
    ids: dict[str, str] = {}
    replies = []
    for step in recording["timeline"]:
        read = step.get("read") or step.get("page")
        if read and read.get("name") != "reload":  # how many reloads a stop sends is a race
            replies += read["frames"]
        elif "checkpoint" in step:
            replies += step["checkpoint"]["cold"]
    return [_comparable({"type": frame["type"], **{field: frame["data"].get(field) for field in READ_FIELDS}}, ids)
            for frame in (*replies, *recording["cold"]) if frame["type"] in READ_REPLIES]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_committed_recording_matches_the_server(name: str, tmp_path: Path) -> None:
    fresh = json.loads(dump(record(name, tmp_path)))
    committed = json.loads((RECORDINGS / f"{name}.json").read_text(encoding="utf-8"))
    assert client_steps(fresh["timeline"]) == client_steps(committed["timeline"])
    assert frame_kinds(fresh["timeline"]) == frame_kinds(committed["timeline"]), (
        f"the server's frames changed; run python -m tests.support.transcript.record_chats --only {name}")
    assert stored_reads(fresh) == stored_reads(committed), (
        f"what a read returns changed; run python -m tests.support.transcript.record_chats --only {name}")


def test_a_recording_goes_stale_when_a_read_returns_something_else(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openprogram.interfaces.server.transcript import reader

    committed = json.loads((RECORDINGS / "page_edge_attach.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(reader, "INITIAL_PAGE_TURNS", 3)
    fresh = json.loads(dump(record("page_edge_attach", tmp_path)))
    assert stored_reads(fresh) != stored_reads(committed)


def test_stored_reads_ignore_clocks_and_generated_ids() -> None:
    def recording(row_id: str, moment: float) -> dict:
        frame = {"type": "session_loaded", "data": {"turns": [{"id": row_id, "rows": [
            {"id": f"{row_id}_reply", "timestamp": moment, "extra": json.dumps({"at": moment, "owner": row_id})}]}]}}
        return {"timeline": [{"read": {"name": "open", "frames": [frame]}}], "cold": []}

    first, second = "7e49da9c30224aedaf49f0f7ba3ff02c", "35cc3b7fcdda1234"
    assert stored_reads(recording(first, 1_794_225_809.1)) == stored_reads(recording(second, 1_794_228_486.9))


def test_stored_reads_ignore_how_long_a_sub_agent_ran() -> None:
    def recording(ran_for: str) -> dict:
        result = f'<agent name="sub task" status="completed">\nreply\nusage: {ran_for} · 0 tool calls\n</agent>'
        frame = {"type": "session_loaded", "data": {"turns": [{"id": "a", "rows": [{"id": "a", "result": result}]}]}}
        return {"timeline": [{"read": {"name": "open", "frames": [frame]}}], "cold": []}

    assert stored_reads(recording("0 s")) == stored_reads(recording("1 s")) == stored_reads(recording("2 min"))

def test_a_sub_agent_card_arrives_as_a_numbered_update_not_a_stream_event(tmp_path: Path) -> None:
    fresh = record("sync_sub_agent", tmp_path)
    assert not [s for s in fresh["timeline"] if s.get("frame", {}).get("data", {}).get("event", {}).get("type") == "sub_agent"]
    assert any(c.get("kind") == "item_upsert" and c.get("list") == "attachCards"
               for s in fresh["timeline"] for c in s.get("frame", {}).get("data", {}).get("changes", []))


def test_the_recorder_adds_nothing_to_a_frame(tmp_path: Path) -> None:
    frame = {"type": "chat_response", "data": {"type": "stream_event", "session_id": "x", "event": {"type": "text"}}}
    with live_chat(tmp_path) as chat:
        chat.broadcast(json.dumps(frame))
    assert chat.timeline == [{"frame": frame}]
    assert isinstance(chat.socket, RecordingSocket)


def test_a_recording_starts_with_the_read_a_browser_makes_when_it_opens() -> None:
    for name in SCENARIOS:
        committed = json.loads((RECORDINGS / f"{name}.json").read_text(encoding="utf-8"))
        first = committed["timeline"][0]
        assert first.get("read", {}).get("name") == "open", name
        assert first["read"]["frames"][0]["data"]["turns"] == [], name


def test_a_stuck_run_fails_teardown_and_still_restores_the_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openprogram.interfaces.server import server
    from openprogram.operations.worker import lock as worker_lock_module

    monkeypatch.setattr(live_server, "_WAIT_SECONDS", 0.05)
    broadcast, lock_path = server._broadcast, worker_lock_module.lock_path
    with pytest.raises(AssertionError, match="the run slot clearing at teardown"):
        with live_chat(tmp_path):
            monkeypatch.setattr(server, "_is_run_active", lambda _session_id: True)
    assert server._broadcast is broadcast
    assert worker_lock_module.lock_path is lock_path


def test_every_old_shape_in_the_design_has_a_case() -> None:
    assert set(OLD_SHAPES) == DESIGN_SHAPES


@pytest.mark.parametrize("name", sorted(OLD_SHAPES))
def test_committed_old_shape_matches_the_store(name: str, tmp_path: Path) -> None:
    committed = json.loads((RECORDINGS.parent / "old-shapes.json").read_text(encoding="utf-8"))
    expected = next(case for case in committed if case["name"] == name)
    with live_chat(tmp_path) as chat:
        built = json.loads(json.dumps(build_case(name, chat), default=str))
        stored = chat.sessions.get_messages(chat.session_id)
    assert built["frames"][0]["data"]["turns"] == expected["frames"][0]["data"]["turns"]
    assert {key: value for key, value in built.items() if key != "frames"} == {
        key: value for key, value in expected.items() if key != "frames"}, "run python -m tests.support.transcript.old_shapes"
    assert stored, "the case wrote through the real store"


def _stored_row(name: str, row_id: str, root: Path) -> dict:
    """One row of an old-shape case as the store hands it to the projection."""
    from openprogram.interfaces.server.persistence import aggregate_tool_messages

    root.mkdir(parents=True, exist_ok=True)
    with live_chat(root) as chat:
        OLD_SHAPES[name](Rows(chat))
        rows = aggregate_tool_messages([dict(row) for row in chat.sessions.get_messages(chat.session_id)])
    return next(row for row in rows if row.get("id") == row_id)


def test_a_predecessor_only_attach_has_no_caller_edge(tmp_path: Path) -> None:
    only = _stored_row("attach_predecessor_only", "attach-1", tmp_path / "only")
    assert not only.get("caller")
    assert only["predecessor"] == "a_reply"
    assert _stored_row("attach_both_edges", "attach-1", tmp_path / "both")["caller"] == "a_reply"


def test_a_manual_attach_carries_the_flag_the_client_branches_on(tmp_path: Path) -> None:
    assert _stored_row("attach_manual", "attach-1", tmp_path)["attach"]["manual"] is True


def test_a_code_node_without_a_call_id_reaches_its_reply_by_the_caller_edge(tmp_path: Path) -> None:
    reply = _stored_row("code_node_without_call_id", "a_reply", tmp_path)
    assert [call["tool_call_id"] for call in reply["tool_calls"]] == ["code-1"]


def test_no_old_shape_is_left_as_a_todo() -> None:
    """Every finding an old shape reproduced is fixed, so each case states only what it draws."""
    committed = json.loads((RECORDINGS.parent / "old-shapes.json").read_text(encoding="utf-8"))
    assert [case["name"] for case in committed if case.get("pending") or "todo" in case] == []


def test_rows_write_to_a_real_store(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        rows = Rows(chat)
        rows.turn("a")
        rows.finish()
        assert chat.sessions.get_session(chat.session_id)["head_id"] == "a_reply"


def test_recording_is_independent_of_materialized_default_store_alias(tmp_path, monkeypatch):
    """A prior fixture's lazy export must not redirect error folding elsewhere."""
    import openprogram.state.store as public_store
    from openprogram.state.store.session import session_store

    stale = session_store.SessionStore(tmp_path / "earlier-fixture")
    # This is the leftover state produced by patching the source default_store
    # before its lazy public export, then undoing both in reverse order.
    monkeypatch.setattr(public_store, "default_store", lambda: stale)
    monkeypatch.setattr(session_store, "default_store", lambda: stale)
    fresh_home = tmp_path / "recording"
    fresh_home.mkdir()
    fresh = json.loads(dump(record("provider_error", fresh_home)))
    committed = json.loads((RECORDINGS / "provider_error.json").read_text())
    assert stored_reads(fresh) == stored_reads(committed)
    assert stale.get_messages(live_server.SESSION_ID) == []


@pytest.mark.parametrize("event_type", ["tool_use", "tool_result"])
def test_parked_recording_frames_match_version_three_socket(tmp_path, event_type):
    import asyncio
    from openprogram.interfaces.server.ws_delivery import QueuedWebSocket

    payload = json.dumps({"type": "chat_response", "data": {
        "type": "stream_event", "session_id": live_server.SESSION_ID,
        "event": {"type": event_type, "name": "lookup"},
    }})
    marker = {"type": "read_finished", "data": {}}

    async def queued_socket():
        frames = []
        socket = QueuedWebSocket(RecordingSocket(frames.append), asyncio.get_running_loop())
        socket.start()
        try:
            await socket.send_text(payload)
            end = socket.park_read(live_server.SESSION_ID)
            await socket.send_text(payload)
            end()
            # A queued marker confirms all earlier delivery work completed.
            await socket.send_text(json.dumps(marker))
            return frames
        finally:
            await socket.stop()

    production_frames = asyncio.run(queued_socket())
    assert production_frames == [marker]
    with live_chat(tmp_path) as chat:
        chat.broadcast(payload)
        end = chat.parking.begin(chat.session_id)
        chat.broadcast(payload)
        assert not chat.timeline
        end()
        chat.broadcast(json.dumps(marker))
        assert [step["frame"] for step in chat.timeline] == production_frames
