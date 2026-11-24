"""A supervisor that ends without writing its command's end leaves the
record unknown; a running command writes nothing to show it lives."""
from __future__ import annotations

from openprogram.host._platform import process_exits
from openprogram.host.processes import record_changes
from openprogram.host.processes.store import ProcessStore


def _running(store, pid=4321, status="running"):
    record = store.create(session_id="conversation", execution_id=None, tool_call_id=None,
                          command="sleep 100", cwd="/work", backend_id="local")
    return store.update(record["id"], status=status, supervisor_pid=pid, supervisor_identity="birth")


def test_the_ended_supervisor_turns_its_live_record_unknown(tmp_path):
    store = ProcessStore(tmp_path / "processes.db")
    record = _running(store)
    assert not store.supervisor_ended(record["id"], 999), "another pid is not its supervisor"
    assert store.supervisor_ended(record["id"], 4321)
    after = store.get(record["id"])
    assert after["status"] == "unknown" and after["ended_at"] is None and after["lost_at"]
    finished = _running(store, status="exited")
    assert not store.supervisor_ended(finished["id"], 4321), "an end it wrote stands"


def test_follow_supervisors_marks_what_ended_and_follows_every_live_one(tmp_path, monkeypatch):
    store = ProcessStore(tmp_path / "processes.db")
    gone, live = _running(store, pid=11), _running(store, pid=12)
    _running(store, pid=13, status="exited")
    followed = []
    monkeypatch.setattr(process_exits, "watch_process", lambda *args, **kw: followed.append(args))

    assert record_changes.follow_supervisors(store, [f"{gone['id']}/11"]) == 1

    assert store.get(gone["id"])["status"] == "unknown"
    assert followed == [(12, "birth", record_changes.SUPERVISORS_TOPIC, f"{live['id']}/12")]
