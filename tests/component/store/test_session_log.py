"""The append-only session log: records, fold, damage."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openprogram.state.store.session import session_log
from openprogram.state.store.session.session_log import (
    SessionLog,
    decode_line,
    encode_record,
    fold_records,
    read_log_file,
)


def _node(node_id: str, seq: int, **extra) -> dict:
    return {"id": node_id, "seq": seq, "role": "user", "metadata": {}, **extra}


def test_a_line_round_trips_and_its_checksum_catches_a_changed_byte():
    line = encode_record(7, "node.put", {"payload": {"id": "n", "output": "中文"}})[:-1]
    assert decode_line(line)["data"]["payload"]["output"] == "中文"
    assert decode_line(line.replace("中".encode(), "文".encode())) is None


def test_the_fold_applies_puts_patches_deletes_and_meta(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "user", "a", _node("a", 0))
    log.write_history(1, "user", "b", _node("b", 1, output="old"))
    log.patch_node("b", {"output": "new"}, {"status": "done"})
    log.write_history(2, "user", "c", _node("c", 2))
    log.delete_nodes(["c"])
    log.write_meta({"title": "t", "head_id": "b"})
    log.write_meta({"head_id": "a"})

    nodes = {n["id"]: n for n in SessionLog(tmp_path / "s").list_history()}
    assert set(nodes) == {"a", "b"}
    assert nodes["b"]["output"] == "new" and nodes["b"]["metadata"]["status"] == "done"
    assert SessionLog(tmp_path / "s").read_meta() == {"head_id": "a"}


def test_large_stream_metadata_stays_small_and_replays_losslessly(tmp_path: Path):
    root = tmp_path / "s"
    log = SessionLog(root)
    log.write_history(0, "assistant", "reply", _node("reply", 0))
    extra = json.dumps({"blocks": [{"type": "tool", "result": "mail tree " * 6000}]})
    log.patch_node("reply", {"output": "partial"}, {"extra": extra}, quiet=True)

    assert log.main_path.stat().st_size < len(extra) // 10
    assert SessionLog(root).list_history()[0]["metadata"]["extra"] == extra


def test_meta_writes_record_only_what_changed(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    log.write_meta({"title": "t", "branches": {"x": 1}})
    log.write_meta({"title": "t", "branches": {"x": 1}, "head_id": "n"})
    log.write_meta({"title": "t", "branches": {"x": 1}, "head_id": "n"})

    records = read_log_file(log.main_path)
    assert [r["data"]["set"] for r in records[1:]] == [{"head_id": "n"}]


def test_earlier_versions_stay_in_the_log(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "user", "a", _node("a", 0, output="first"))
    log.patch_node("a", {"output": "second"})
    log.delete_nodes(["a"])

    text = log.main_path.read_text(encoding="utf-8")
    assert "first" in text and "second" in text
    assert log.list_history() == []


def test_a_line_cut_short_is_not_a_record_and_the_next_writer_removes_it(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "user", "a", _node("a", 0))
    with open(log.main_path, "ab") as handle:
        handle.write(b'{"ts":9,"type":"node.put","data":{"payl')

    assert [n["id"] for n in SessionLog(tmp_path / "s").list_history()] == ["a"]

    session_log._prepared_files.clear()
    SessionLog(tmp_path / "s").write_history(1, "user", "b", _node("b", 1))
    assert [r["data"]["payload"]["id"] for r in read_log_file(log.main_path)] == ["a", "b"]


def test_reading_stops_at_a_damaged_line(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "user", "a", _node("a", 0))
    with open(log.main_path, "ab") as handle:
        handle.write(b"garbage\n")
    log.write_history(1, "user", "b", _node("b", 1))

    assert [r["data"]["payload"]["id"] for r in read_log_file(log.main_path)] == ["a"]


def test_a_process_without_the_worker_lock_writes_its_own_file(tmp_path: Path):
    session_log.release_main_writer()
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "code", "child", _node("child", 0))

    assert not log.main_path.exists()
    assert len(list(log.agent_directory.glob("*.jsonl"))) == 1
    assert [n["id"] for n in SessionLog(tmp_path / "s").list_history()] == ["child"]


def test_records_from_two_processes_fold_in_time_order(tmp_path: Path):
    main = SessionLog(tmp_path / "s")
    main.write_history(0, "user", "a", _node("a", 0, output="main"))
    script = (
        "import sys;from openprogram.state.store.session.session_log import SessionLog;"
        "SessionLog(sys.argv[1]).patch_node('a',{'output':'child'})"
    )
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "s")], check=True)

    assert main.stale()
    assert main.list_history()[0]["output"] == "child"
    assert len(list(main.agent_directory.glob("*.jsonl"))) == 1


def test_the_fold_reads_only_what_was_appended(tmp_path: Path, monkeypatch):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "user", "a", _node("a", 0, content="one"))
    assert [node["id"] for node in log.list_history()] == ["a"]
    offsets = []
    original = session_log.read_appended
    monkeypatch.setattr(session_log, "read_appended",
                        lambda path, start=0, **kw: (offsets.append(start), original(path, start, **kw))[1])
    log.write_history(1, "user", "b", _node("b", 1))
    assert [node["id"] for node in log.list_history()] == ["a", "b"]
    assert len(offsets) == 1 and offsets[0] > 0


def test_a_later_patch_leaves_an_earlier_read_alone(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "user", "a", _node("a", 0, content="one"))
    before = log.list_history()
    log.patch_node("a", {"content": "two"})
    assert before[0]["content"] == "one"
    assert log.list_history()[0]["content"] == "two"


def test_a_line_that_sorts_early_folds_in_time_order(tmp_path: Path, monkeypatch):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "user", "a", _node("a", 0, content="old"))
    log.list_history()
    agents = log.agent_directory
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "other.jsonl").write_bytes(encode_record(1, "node.patch", {"id": "a", "set": {"content": "earliest"}}))
    # The record is older than the put already applied, so the put still wins.
    assert log.list_history()[0]["content"] == "old"


def test_a_replaced_file_is_read_again_from_the_start(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "user", "a", _node("a", 0))
    log.list_history()
    log.main_path.write_bytes(encode_record(5, "node.put", {"payload": _node("z", 0)}))
    assert [node["id"] for node in log.list_history()] == ["z"]


def test_state_documents_live_in_the_log_with_their_history(tmp_path: Path):
    from openprogram.state.store.session.session_store.service import SessionStore

    store = SessionStore(tmp_path)
    store._open("s", create_if_missing=True)[0]._ensure_init()
    todos = store.state_file("s", "todos.json")
    assert not todos.exists()
    with pytest.raises(FileNotFoundError):
        todos.read_text()
    todos.write_text('{"v": 1}')
    todos.write_text('{"v": 2}')
    assert store.state_file("s", "todos.json").read_text() == '{"v": 2}'
    assert not (store._session_dir("s") / "todos.json").exists()
    session = store._session_dir("s")
    versions = [record["data"]["content"]
                for path in (session / "main.jsonl", *(session / "agents").glob("*.jsonl"))
                for record in read_log_file(path) if record["type"] == "file.put"]
    assert versions == ['{"v": 1}', '{"v": 2}']
    assert store.state_file("s", "todos.json").with_suffix(".lock").parent == store._session_dir("s")


def test_one_append_picks_its_file_once_when_the_writer_role_changes(tmp_path, monkeypatch):
    from itertools import cycle

    answers = cycle([True, False])
    monkeypatch.setattr(session_log, "is_main_writer", lambda: next(answers))
    log = session_log.SessionLog(tmp_path / "session")

    log.append_state_file("first.json", "1")
    log.append_state_file("second.json", "2")

    assert log.main_path.exists()
    assert list(log.agent_directory.glob("*.jsonl"))


def test_a_stream_line_folds_onto_the_reply(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    trace = {"blocks": [{"type": "text", "text": "Hel"}], "stream_seq": 1, "kept": True}
    log.write_history(0, "assistant", "r", _node("r", 0, output="Hel"))
    log.patch_node("r", {"output": "Hel"}, {"extra": json.dumps(trace)})
    log.stream_node("r", {"text": [0, "lo"], "output_tail": "lo", "stream_seq": 2}, quiet=True)
    log.stream_node("r", {"from": 1, "blocks": [{"type": "tool", "result": None}], "stream_seq": 3})
    log.stream_node("r", {"from": 1, "blocks": [{"type": "tool", "result": "ok"}], "stream_seq": 4,
                          "output": "Hello!"})

    node = SessionLog(tmp_path / "s").list_history()[0]
    assert node["output"] == "Hello!"
    assert json.loads(node["metadata"]["extra"]) == {
        "blocks": [{"type": "text", "text": "Hello"}, {"type": "tool", "result": "ok"}],
        "stream_seq": 4, "kept": True}


def test_a_trace_a_later_line_replaced_is_never_unpacked(tmp_path: Path, monkeypatch):
    """A cold open once decompressed every stored version of every reply's trace."""
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "assistant", "r", _node("r", 0))
    for n in range(30):
        extra = json.dumps({"blocks": [{"type": "text", "text": f"{n:03d}" * 4000}]})
        log.patch_node("r", {"output": str(n)}, {"extra": extra}, quiet=True)
    unpacked = []
    original = session_log.unpack_extra
    monkeypatch.setattr(session_log, "unpack_extra", lambda packed: (unpacked.append(1), original(packed))[1])

    node = SessionLog(tmp_path / "s").list_history()[0]
    assert json.loads(node["metadata"]["extra"])["blocks"][0]["text"].startswith("029")
    assert len(unpacked) == 1


def test_a_long_reply_is_put_together_once_however_many_lines_it_streamed_in(tmp_path: Path, monkeypatch):
    """Each line once rebuilt the reply's whole text: 30,000 deltas of a 6.5 MB reply folded in 8.3 s."""
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "assistant", "r", _node("r", 0, output=""))
    log.patch_node("r", {"output": ""}, {"extra": json.dumps({"blocks": [{"type": "thinking", "text": ""}]})})
    pieces = [f"piece {n} " for n in range(2_000)]
    for n, piece in enumerate(pieces[:1_000]):
        log.stream_node("r", {"text": [0, piece], "stream_seq": n}, quiet=True)
    log.stream_node("r", {"from": 1, "blocks": [{"type": "text", "text": ""}], "stream_seq": 1_000}, quiet=True)
    for n, piece in enumerate(pieces[1_000:], 1_001):
        log.stream_node("r", {"text": [1, piece], "output_tail": piece, "stream_seq": n}, quiet=True)
    joins = []
    original = session_log.join_text
    monkeypatch.setattr(session_log, "join_text",
                        lambda current, added: (joins.append(len(added)), original(current, added))[1])

    node = SessionLog(tmp_path / "s").list_history()[0]

    assert node["output"] == "".join(pieces[1_000:])
    assert json.loads(node["metadata"]["extra"]) == {
        "blocks": [{"type": "thinking", "text": "".join(pieces[:1_000])},
                   {"type": "text", "text": "".join(pieces[1_000:])}],
        "stream_seq": 2_000}
    # The output and each of the two blocks, each from all of its pieces.
    assert sorted(joins) == [1_000, 1_000, 1_000]


def test_text_streamed_after_a_replaced_output_or_block_lands_on_the_replacement(tmp_path: Path):
    log = SessionLog(tmp_path / "s")
    log.write_history(0, "assistant", "r", _node("r", 0, output="old"))
    log.patch_node("r", {"output": "old"}, {"extra": json.dumps({"blocks": [{"type": "text", "text": "old"}]})})
    log.stream_node("r", {"text": [0, " tail"], "output_tail": " tail"}, quiet=True)
    log.stream_node("r", {"from": 0, "blocks": [{"type": "text", "text": "new"}], "output": "new"}, quiet=True)
    log.stream_node("r", {"text": [0, " one"], "output_tail": " one"}, quiet=True)
    log.patch_node("r", {"output": "set"}, quiet=True)
    log.stream_node("r", {"text": [0, " two"], "output_tail": " two"}, quiet=True)
    log.stream_node("r", {"text": [3, "nowhere"]}, quiet=True)

    node = SessionLog(tmp_path / "s").list_history()[0]
    assert node["output"] == "set two"
    assert json.loads(node["metadata"]["extra"])["blocks"] == [{"type": "text", "text": "new one two"}]

    # A reader that already folded part of the reply gets the same text.
    reader = SessionLog(tmp_path / "s")
    reader.list_history()
    log.stream_node("r", {"text": [0, " three"], "output_tail": " three"}, quiet=True)
    node = reader.list_history()[0]
    assert node["output"] == "set two three"
    assert json.loads(node["metadata"]["extra"])["blocks"] == [{"type": "text", "text": "new one two three"}]


def _filled(root: Path, records: int) -> SessionLog:
    log = SessionLog(root)
    log.write_history(0, "user", "a", _node("a", 0))
    for n in range(records):
        log.patch_node("a", {"output": f"{n} " + "x" * 200})
    log.append_state_file("todos.json", "[1]")
    log.append_turn_record(session_log.RUN_ROWS_RECORD, {"rows": [{"n": 1}]})
    return log


def test_a_cold_fold_reads_the_checkpoint_and_only_the_lines_after_it(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(session_log, "CHECKPOINT_MIN_BYTES", 10_000)
    root = tmp_path / "s"
    log = _filled(root, 200)
    log.list_history()
    assert (root / "checkpoint" / "fold.json").is_file()
    covered = log._writer_path().stat().st_size
    log.patch_node("a", {"output": "after"})

    starts = []
    original = session_log.read_appended
    monkeypatch.setattr(session_log, "read_appended",
                        lambda path, start=0, **kw: (starts.append(start), original(path, start, **kw))[1])
    reopened = SessionLog(root)
    assert reopened.list_history()[0]["output"] == "after"
    assert starts == [covered]
    assert reopened.read_run_rows() == [[{"n": 1}]]


def test_a_checkpoint_of_a_rewritten_log_is_not_used(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(session_log, "CHECKPOINT_MIN_BYTES", 10_000)
    root = tmp_path / "s"
    log = _filled(root, 200)
    log.list_history()
    main = log._writer_path()
    covered = main.stat().st_size
    # The same file, rewritten in place and longer than the part covered.
    lines = [encode_record(1, "node.put", {"payload": _node("z", 0)})]
    while sum(map(len, lines)) <= covered:
        lines.append(encode_record(len(lines) + 1, "node.patch", {"id": "z", "set": {"output": "w" * 300}}))
    inode = main.stat().st_ino
    with open(main, "r+b") as handle:
        handle.truncate(0)
        handle.write(b"".join(lines))
    assert main.stat().st_ino == inode
    assert [node["id"] for node in SessionLog(root).list_history()] == ["z"]


def test_state_files_and_run_rows_read_from_the_checkpoint(tmp_path: Path, monkeypatch):
    from openprogram.state.store.session import session_state_file
    monkeypatch.setattr(session_log, "CHECKPOINT_MIN_BYTES", 10_000)
    root = tmp_path / "s"
    _filled(root, 200).list_history()
    session_state_file.forget_directory(root)
    starts = []
    original = session_state_file.read_appended
    monkeypatch.setattr(session_state_file, "read_appended",
                        lambda path, start=0, **kw: (starts.append(start), original(path, start, **kw))[1])
    assert session_state_file.read_files(root) == {"todos.json": "[1]"}
    assert session_state_file.read_run_rows(root) == [[{"n": 1}]]
    assert starts == []
