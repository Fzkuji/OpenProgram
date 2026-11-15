"""A recording sink reads only what was appended since its last call.

A run that records every provider request was killed at 378 MB: the sink
read the whole file again before each request to number the call, so its
memory per request was twice the file's size. The sink now remembers how
far it read.
"""
from __future__ import annotations

import json
import os
import tracemalloc
from pathlib import Path

from openprogram.models.providers.recording import recording
from openprogram.models.providers.recording.recording import RecordingSink


def _call(sink: RecordingSink, marker: str, *, size: int = 0, events: int = 2) -> int:
    call_index = sink.begin_call({
        "model": {"id": marker},
        "context": {"messages": [{"role": "user", "content": "x" * size}]},
        "options": None,
    })
    for event_index in range(events):
        # An event whose text quotes a request row must not count as one.
        sink.append_event(call_index, event_index,
                          {"type": "text", "text": '"type": "request", "call_index": 99'})
    sink.end_call(call_index, events)
    return call_index


def test_two_sinks_on_one_file_number_their_calls_in_one_sequence(tmp_path: Path) -> None:
    path = tmp_path / "shared.jsonl"
    first, second = RecordingSink(path), RecordingSink(path)

    indexes = [_call(sink, f"m{n}") for n, sink in enumerate([first, second] * 4)]

    assert indexes == list(range(8))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["call_index"] for row in rows if row.get("type") == "request"] == list(range(8))


def test_each_call_looks_only_at_the_rows_appended_since_the_last(tmp_path, monkeypatch) -> None:
    sink = RecordingSink(tmp_path / "count.jsonl")
    looked: list[bytes] = []
    real = recording._REQUEST_ROW_HEAD

    class Counting:
        @staticmethod
        def match(raw):
            looked.append(raw)
            return real.match(raw)

    monkeypatch.setattr(recording, "_REQUEST_ROW_HEAD", Counting)
    for n in range(20):
        _call(sink, f"m{n}")

    # Call n looks at request n-1 alone: never the rows it read before, and
    # never an event row, whatever its text.
    assert len(looked) == 19
    assert all(json.loads(raw)["type"] == "request" for raw in looked)
    assert [json.loads(raw)["call_index"] for raw in looked] == list(range(19))


def test_a_request_row_of_another_shape_is_still_counted(tmp_path: Path) -> None:
    path = tmp_path / "other.jsonl"
    sink = RecordingSink(path)
    # A row another writer put in without sorted keys.
    with path.open("ab") as handle:
        handle.write(b'{"type": "request", "call_index": 6, "model": {}}\n')

    assert _call(sink, "next") == 7


def test_the_memory_a_call_takes_does_not_grow_with_the_file(tmp_path: Path) -> None:
    sink = RecordingSink(tmp_path / "big.jsonl")
    for n in range(8):
        _call(sink, f"big{n}", size=1_000_000)
    assert (tmp_path / "big.jsonl").stat().st_size > 8_000_000

    tracemalloc.start()
    try:
        _call(sink, "small")
        _call(sink, "small again")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # The last big request row (1 MB) may be read once; never the 8 MB file.
    assert peak < 3_000_000


def test_a_file_cut_short_is_numbered_again_from_its_start(tmp_path: Path) -> None:
    path = tmp_path / "cut.jsonl"
    sink = RecordingSink(path)
    header = path.stat().st_size
    for n in range(3):
        _call(sink, f"m{n}")

    os.truncate(path, header)

    assert _call(sink, "after") == 0


def test_the_whole_file_is_never_read_as_text(tmp_path, monkeypatch) -> None:
    sink = RecordingSink(tmp_path / "text.jsonl")
    _call(sink, "first")

    def refuse(self, *args, **kwargs):
        raise AssertionError(f"read the whole of {self}")

    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    assert _call(sink, "second") == 1
