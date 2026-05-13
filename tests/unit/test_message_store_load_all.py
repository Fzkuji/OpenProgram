"""Tests for MessageStore.load_all — v2 JSONL startup rehydrate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprogram.webui.messages import Block, Message, MessageStore, SCHEMA_VERSION


def _write_v2_jsonl(root: Path, session_id: str, messages: list[Message]) -> None:
    d = root / session_id
    d.mkdir(parents=True, exist_ok=True)
    with (d / "messages.jsonl").open("w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps({"v": SCHEMA_VERSION, "message": m.to_dict()}) + "\n")


def _msg(msg_id: str, session_id: str, text: str) -> Message:
    m = Message(id=msg_id, session_id=session_id, role="assistant", status="complete")
    m.content.append(Block(type="text", text=text))
    return m


def test_load_all_rehydrates_every_conv(tmp_path):
    _write_v2_jsonl(tmp_path, "conv-a", [_msg("m1", "conv-a", "hi")])
    _write_v2_jsonl(tmp_path, "conv-b", [
        _msg("m2", "conv-b", "one"),
        _msg("m3", "conv-b", "two"),
    ])

    store = MessageStore(persist_dir=tmp_path)
    loaded = store.load_all()

    assert sorted(loaded) == ["conv-a", "conv-b"]
    assert [m.id for m in store.list_for_conv("conv-a")] == ["m1"]
    assert [m.id for m in store.list_for_conv("conv-b")] == ["m2", "m3"]
    # content survives the round trip.
    m1 = store.list_for_conv("conv-a")[0]
    assert m1.content[0].text == "hi"


def test_load_all_ignores_dirs_without_messages_jsonl(tmp_path):
    """v1 layout (messages.json + trees/) lives alongside v2 — we must
    not pick up those dirs or try to read the old format."""
    _write_v2_jsonl(tmp_path, "v2", [_msg("m1", "v2", "ok")])
    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / "messages.json").write_text("[]", encoding="utf-8")

    store = MessageStore(persist_dir=tmp_path)
    loaded = store.load_all()
    assert loaded == ["v2"]


def test_load_all_handles_empty_or_missing_dir(tmp_path):
    assert MessageStore(persist_dir=tmp_path).load_all() == []
    missing = tmp_path / "not-there"
    assert MessageStore(persist_dir=missing).load_all() == []


def test_load_all_is_idempotent(tmp_path):
    _write_v2_jsonl(tmp_path, "c", [_msg("m1", "c", "a")])
    store = MessageStore(persist_dir=tmp_path)
    store.load_all()
    store.load_all()
    msgs = store.list_for_conv("c")
    assert len(msgs) == 1
    assert msgs[0].id == "m1"


def test_load_all_skips_corrupt_file_and_continues(tmp_path, capsys):
    _write_v2_jsonl(tmp_path, "good", [_msg("m1", "good", "x")])
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "messages.jsonl").write_text("{ not json\n", encoding="utf-8")

    store = MessageStore(persist_dir=tmp_path)
    loaded = store.load_all()

    assert "good" in loaded
    assert "bad" not in loaded
    # Good conv still rehydrated.
    assert store.list_for_conv("good")[0].id == "m1"


def test_load_all_skips_wrong_schema_version(tmp_path):
    """Records from a future schema version we don't understand get
    dropped silently — safer than crashing the whole startup."""
    d = tmp_path / "conv-x"
    d.mkdir()
    (d / "messages.jsonl").write_text(
        json.dumps({"v": 999, "message": {"id": "mX", "session_id": "conv-x", "role": "assistant"}}) + "\n",
        encoding="utf-8",
    )
    store = MessageStore(persist_dir=tmp_path)
    loaded = store.load_all()
    assert loaded == ["conv-x"]
    # Nothing from that file made it in.
    assert store.list_for_conv("conv-x") == []


def test_load_all_preserves_seq_and_status(tmp_path):
    m = _msg("m1", "c", "done")
    m.seq = 7
    m.status = "complete"
    _write_v2_jsonl(tmp_path, "c", [m])

    store = MessageStore(persist_dir=tmp_path)
    store.load_all()
    restored = store.list_for_conv("c")[0]
    assert restored.seq == 7
    assert restored.status == "complete"
