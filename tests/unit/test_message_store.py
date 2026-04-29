"""Unit tests for openprogram.webui.messages."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openprogram.webui.messages import (
    MAX_DELTA_CATCHUP,
    Block,
    Message,
    MessageStore,
    SCHEMA_VERSION,
)


def _capture(store: MessageStore, conv_id: str) -> list[dict]:
    frames: list[dict] = []
    store.subscribe(conv_id, lambda _cid, f: frames.append(f))
    return frames


def test_create_emits_snapshot():
    s = MessageStore()
    frames = _capture(s, "c")
    msg = s.create("c", "assistant", status="streaming")
    assert len(frames) == 1
    assert frames[0]["type"] == "message.snapshot"
    assert frames[0]["message"]["id"] == msg.id
    assert frames[0]["message"]["status"] == "streaming"


def test_text_accumulates_into_block():
    s = MessageStore()
    msg = s.create("c", "assistant", status="streaming")
    s.add_block(msg.id, Block(type="text"))
    bid = msg.content[0].id
    s.append_text(msg.id, bid, "foo")
    s.append_text(msg.id, bid, "bar")
    assert msg.content[0].text == "foobar"
    assert msg.seq == 3   # add_block, append, append


def test_commit_sets_terminal_status_and_emits_commit_frame():
    s = MessageStore()
    frames = _capture(s, "c")
    msg = s.create("c", "assistant", status="streaming")
    s.commit(msg.id, status="complete", usage={"input": 1}, stop_reason="stop")
    assert msg.status == "complete"
    assert msg.usage == {"input": 1}
    kinds = [f["type"] for f in frames]
    assert "message.commit" in kinds
    # And the same transition should also be in the delta stream so sync can
    # replay it.
    delta_ops = [f["patch"]["op"] for f in frames if f["type"] == "message.delta"]
    assert "set_status" in delta_ops


def test_sync_returns_nothing_when_caught_up():
    s = MessageStore()
    msg = s.create("c", "assistant")
    s.add_block(msg.id, Block(type="text"))
    assert s.sync("c", {msg.id: msg.seq}) == []


def test_sync_replays_delta_window_on_small_gap():
    s = MessageStore()
    msg = s.create("c", "assistant")
    s.add_block(msg.id, Block(type="text"))
    bid = msg.content[0].id
    for chunk in ["a", "b", "c", "d"]:
        s.append_text(msg.id, bid, chunk)
    # Client saw seq=2 (after add_block + first append). It should get
    # three delta frames for the remaining appends.
    frames = s.sync("c", {msg.id: 2})
    assert all(f["type"] == "message.delta" for f in frames)
    assert [f["seq"] for f in frames] == [3, 4, 5]


def test_sync_falls_back_to_snapshot_when_ring_evicted():
    s = MessageStore()
    msg = s.create("c", "assistant")
    s.add_block(msg.id, Block(type="text"))
    bid = msg.content[0].id
    # Push more than the ring can hold.
    for i in range(MAX_DELTA_CATCHUP + 20):
        s.append_text(msg.id, bid, "x")
    frames = s.sync("c", {msg.id: 1})
    assert len(frames) == 1
    assert frames[0]["type"] == "message.snapshot"


def test_sync_sends_snapshot_for_unknown_message():
    s = MessageStore()
    msg = s.create("c", "assistant")
    frames = s.sync("c", {})
    assert len(frames) == 1
    assert frames[0]["type"] == "message.snapshot"
    assert frames[0]["message"]["id"] == msg.id


def test_persistence_round_trip(tmp_path: Path):
    s1 = MessageStore(persist_dir=tmp_path)
    msg = s1.create("c", "assistant", status="streaming")
    s1.add_block(msg.id, Block(type="text"))
    bid = msg.content[0].id
    s1.append_text(msg.id, bid, "hello world")
    s1.commit(msg.id, status="complete", stop_reason="stop")

    s2 = MessageStore(persist_dir=tmp_path)
    s2.load_conv("c")
    loaded = s2.get(msg.id)
    assert loaded is not None
    assert loaded.content[0].text == "hello world"
    assert loaded.status == "complete"
    assert loaded.seq == msg.seq


def test_persistence_uses_schema_version(tmp_path: Path):
    s = MessageStore(persist_dir=tmp_path)
    msg = s.create("c", "assistant")
    s.commit(msg.id, status="complete")
    path = tmp_path / "c" / "messages.jsonl"
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["v"] == SCHEMA_VERSION


def test_block_to_dict_trims_type_specific_fields():
    txt = Block(type="text", text="hi")
    d = txt.to_dict()
    assert "tool_call_id" not in d
    assert "image_uri" not in d

    tool = Block(type="tool_use", tool_name="bash", tool_arguments={"cmd": "ls"})
    d = tool.to_dict()
    assert "text" not in d
    assert d["tool_name"] == "bash"


def test_unsubscribe_stops_delivery():
    s = MessageStore()
    received: list[dict] = []
    unsub = s.subscribe("c", lambda _cid, f: received.append(f))
    msg = s.create("c", "assistant")
    assert len(received) == 1
    unsub()
    s.commit(msg.id)
    # commit emits two frames (delta + commit); subscriber should've seen none.
    assert len(received) == 1


def test_update_block_fields():
    s = MessageStore()
    msg = s.create("c", "assistant")
    s.add_block(msg.id, Block(type="tool_use", tool_name="bash"))
    bid = msg.content[0].id
    s.update_block(msg.id, bid, tool_arguments={"cmd": "ls -la"})
    assert msg.content[0].tool_arguments == {"cmd": "ls -la"}


def test_append_text_rejects_missing_block():
    s = MessageStore()
    msg = s.create("c", "assistant")
    with pytest.raises(KeyError):
        s.append_text(msg.id, "does_not_exist", "x")


def test_listener_exceptions_dont_break_broadcast():
    s = MessageStore()
    hits = []
    s.subscribe("c", lambda _cid, _f: (_ for _ in ()).throw(RuntimeError("boom")))
    s.subscribe("c", lambda _cid, f: hits.append(f))
    s.create("c", "assistant")
    assert len(hits) == 1
