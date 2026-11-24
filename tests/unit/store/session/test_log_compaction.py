"""Replaced reply traces leave a session's logs; what the session folds to stays."""
from __future__ import annotations

import json

import pytest

from openprogram.state.store.session.files import log_compaction
from openprogram.state.store.session.files.session_log import SessionLog


def _trace(blocks: int) -> str:
    return json.dumps({"blocks": [{"type": "text", "text": f"step {index} " + "x" * 400}
                                  for index in range(blocks)]})


def _session(tmp_path):
    directory = tmp_path / "session"
    log = SessionLog(directory)
    log.write_history(1, "user", "ask", {"id": "ask", "role": "user", "output": "do it"})
    for node in ("reply", "other"):
        log.write_history(2, "llm", node, {"id": node, "role": "llm", "output": "", "metadata": {}})
    for step in range(1, 41):
        # A long trace is stored compressed, a short one as it is: both are replaced whole.
        log.patch_node("reply", {"output": f"at {step}"}, {"extra": _trace(step), "status": "running"})
        log.patch_node("other", {}, {"extra": json.dumps({"blocks": [{"type": "text", "text": str(step)}]})})
    log.stream_node("reply", {"text": [0, " and more"], "stream_seq": 7})
    log.patch_node("reply", {}, {"status": "completed"})
    return directory


def _state(directory):
    log = SessionLog(directory)
    return log.list_history(), log.read_meta()


def test_replaced_traces_are_dropped_and_the_session_reads_the_same(tmp_path):
    directory = _session(tmp_path)
    before = _state(directory)
    reply = next(node for node in before[0] if node["id"] == "reply")
    assert json.loads(reply["metadata"]["extra"])["blocks"][0]["text"].endswith(" and more")

    planned = log_compaction.compact_session(directory, apply=False)
    assert planned.dropped == 78 and planned.after < planned.before / 5
    assert sum(path.stat().st_size for path in log_compaction.session_logs(directory)) == planned.before, \
        "looking at what compacting would do changes no file"

    done = log_compaction.compact_session(directory)
    assert done == planned
    assert sum(path.stat().st_size for path in log_compaction.session_logs(directory)) == done.after
    assert _state(directory) == before
    assert not list(directory.rglob("*.compact.tmp"))
    assert log_compaction.compact_session(directory).dropped == 0, "a compacted session has nothing left to drop"


def test_logs_that_would_fold_differently_are_left_alone(tmp_path, monkeypatch):
    directory = _session(tmp_path)
    before = (directory / "main.jsonl").read_bytes()
    rewrite = log_compaction._rewrite

    def lossy(source, target):
        dropped = rewrite(source, target)
        target.write_bytes(target.read_bytes().rsplit(b"\n", 2)[0] + b"\n")
        return dropped

    monkeypatch.setattr(log_compaction, "_rewrite", lossy)
    with pytest.raises(log_compaction.CompactionMismatch):
        log_compaction.compact_session(directory)
    assert (directory / "main.jsonl").read_bytes() == before
    assert not list(directory.rglob("*.compact.tmp"))
