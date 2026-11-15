"""One append-only file keeps keyed records for every process sharing it."""
from __future__ import annotations

from openprogram.state.store import record_log as module
from openprogram.state.store.record_log import RecordLog


def _put(log, key, value):
    with log.locked() as held:
        held.put(key, {"value": value})


def _records(log):
    with log.locked() as held:
        return dict(held.records)


def test_records_come_back_from_the_file(tmp_path):
    path = tmp_path / "records.jsonl"
    log = RecordLog(path)
    _put(log, "a", 1)
    _put(log, "b", 2)
    with log.locked() as held:
        held.delete(["a"])

    assert _records(RecordLog(path)) == {"b": {"value": 2}}


def test_a_second_opener_sees_later_writes(tmp_path):
    path = tmp_path / "records.jsonl"
    first, second = RecordLog(path), RecordLog(path)
    _put(first, "a", 1)
    assert _records(second) == {"a": {"value": 1}}
    _put(second, "a", 2)
    assert _records(first) == {"a": {"value": 2}}


def test_a_torn_last_line_is_dropped_and_the_next_write_survives(tmp_path):
    path = tmp_path / "records.jsonl"
    _put(RecordLog(path), "a", 1)
    with path.open("ab") as handle:
        handle.write(b'{"k":"b","r":{"val')

    log = RecordLog(path)
    assert _records(log) == {"a": {"value": 1}}
    _put(log, "c", 3)

    assert _records(RecordLog(path)) == {"a": {"value": 1}, "c": {"value": 3}}


def test_a_rewrite_keeps_every_record_including_the_one_that_triggered_it(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_DEAD_LINE_ALLOWANCE", 2)
    path = tmp_path / "records.jsonl"
    log, other = RecordLog(path), RecordLog(path)
    _put(other, "start", 0)
    assert _records(other)
    for index in range(12):
        _put(log, "same", index)

    assert path.read_text(encoding="utf-8").count("\n") < 12
    expected = {"start": {"value": 0}, "same": {"value": 11}}
    assert _records(RecordLog(path)) == expected
    assert _records(other) == expected


def test_old_superseded_browser_pages_are_forgotten():
    import sqlite3

    from openprogram.programs.applications.browser.pages import (
        SUPERSEDED_RETENTION_SECONDS,
        _forget_old_superseded,
    )

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE browser_resources (resource_id TEXT, lifecycle TEXT, updated_at REAL)")
    db.execute("CREATE TABLE browser_resource_control (resource_id TEXT)")
    db.execute("CREATE TABLE browser_resource_presentation (resource_id TEXT)")
    now = 1_000_000.0
    old = now - SUPERSEDED_RETENTION_SECONDS - 1
    for resource_id, lifecycle, updated_at in (
        ("old", "superseded", old), ("recent", "superseded", now), ("open", "connected", old),
    ):
        db.execute("INSERT INTO browser_resources VALUES (?,?,?)", (resource_id, lifecycle, updated_at))
        db.execute("INSERT INTO browser_resource_control VALUES (?)", (resource_id,))
        db.execute("INSERT INTO browser_resource_presentation VALUES (?)", (resource_id,))

    _forget_old_superseded(db, now)

    for table in ("browser_resources", "browser_resource_control", "browser_resource_presentation"):
        assert sorted(row[0] for row in db.execute(f"SELECT resource_id FROM {table}")) == ["open", "recent"]


def test_the_file_holds_exactly_the_bytes_counted(tmp_path):
    """LF line ends on every system: a text-mode descriptor on Windows would
    write CRLF and leave the counted offset short of the file."""
    path = tmp_path / "records.jsonl"
    log = RecordLog(path)
    _put(log, "a", "中文")
    _put(log, "b", 2)

    raw = path.read_bytes()
    assert b"\r" not in raw
    assert raw.count(b"\n") == 2
    assert log._offset == len(raw)


def test_descriptors_are_opened_binary(tmp_path, monkeypatch):
    """The flag Windows needs, seen through a stand-in for ``os.O_BINARY``;
    not run on Windows."""
    import os

    binary = 1 << 30
    seen: list[int] = []
    real_open = os.open

    def recording_open(path, flags, *args, **kwargs):
        if str(path).startswith(str(tmp_path / "records.jsonl")):
            seen.append(flags)
        return real_open(path, flags & ~binary, *args, **kwargs)

    monkeypatch.setattr(module, "O_BINARY", binary)
    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(module, "_DEAD_LINE_ALLOWANCE", 0)
    log = RecordLog(tmp_path / "records.jsonl")
    for value in range(4):  # the repeated key forces a rewrite as well
        _put(log, "a", value)

    assert len(seen) >= 5
    assert all(flags & binary for flags in seen)
