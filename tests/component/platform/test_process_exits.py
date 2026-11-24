"""The operating system says when a watched process ends, and the Worker
marks a command whose supervisor died the moment it dies."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from openprogram.host._platform import changes, process_exits
from openprogram.host._platform.processes import process_start_token
from openprogram.host.processes import record_changes
from openprogram.host.processes.store import ProcessStore, process_identity


@pytest.fixture
def following(monkeypatch):
    monkeypatch.setattr(process_exits, "_watches", set())
    monkeypatch.setattr(process_exits, "_queued", [])
    process_exits.start()
    try:
        yield
    finally:
        process_exits.stop()


def _child(seconds: float) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])


def _wait(listener, until: float) -> set[str]:
    seen: set[str] = set()
    while not seen and time.monotonic() < until:
        seen = listener.wait(max(0.0, until - time.monotonic()))
    return seen


def test_a_child_that_exits_is_announced_promptly(following):
    child = _child(0.4)
    try:
        with changes.listen("exits") as listener:
            began = time.monotonic()
            process_exits.watch_process(child.pid, process_start_token(child.pid), "exits", "child")
            assert listener.wait(0.2) == set(), "a running process is not announced"
            seen = _wait(listener, time.monotonic() + 3.0)
            ended_at = time.monotonic()
            assert seen == {"exits:child"}
            child.wait(timeout=5)
    finally:
        child.kill()
        child.wait()
    # Seen at its exit, well before the fallback look would have.
    assert ended_at - began < 3.0 < process_exits.FALLBACK_SECONDS
    assert process_exits.watched() == 0


def test_a_pid_now_owned_by_another_process_is_announced_at_once(following):
    child = _child(30)
    try:
        with changes.listen("exits") as listener:
            process_exits.watch_process(child.pid, "born another time", "exits", "reused")
            assert _wait(listener, time.monotonic() + 3.0) == {"exits:reused"}
    finally:
        child.kill()
        child.wait()


def test_a_killed_supervisor_leaves_its_record_unknown_at_once(following, tmp_path):
    store = ProcessStore(tmp_path / "processes.db")
    supervisor = _child(30)
    try:
        record = store.create(session_id="conversation", execution_id=None, tool_call_id=None,
                              command="sleep 30", cwd=str(tmp_path), backend_id="local")
        store.update(record["id"], status="running", supervisor_pid=supervisor.pid,
                     supervisor_identity=process_identity(supervisor.pid))
        with changes.listen(record_changes.SUPERVISORS_TOPIC) as listener:
            record_changes.follow_supervisors(store)
            assert listener.wait(0.2) == set()
            killed_at = time.monotonic()
            supervisor.kill()
            seen = _wait(listener, killed_at + 3.0)
            assert time.monotonic() - killed_at < 3.0
        record_changes.follow_supervisors(store, [key.split(":", 1)[1] for key in seen])
        assert store.get(record["id"])["status"] == "unknown"
    finally:
        supervisor.kill()
        supervisor.wait()
