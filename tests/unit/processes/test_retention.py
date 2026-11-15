"""Finished process records leave the store a day after they end."""
from __future__ import annotations

import pytest

from openprogram.host.processes.store import RETENTION_SECONDS, ProcessStore


@pytest.fixture
def store(tmp_path):
    return ProcessStore(tmp_path / "processes.db")


def _record(store, **fields):
    record = store.create(session_id="conversation", execution_id=None, tool_call_id=None,
                          command="npm test", cwd="/work", backend_id="local")
    store.append_output(record["id"], b"ok\n")
    return store.update(record["id"], **fields)


def test_records_past_retention_are_dropped_with_their_output(store):
    now = 1_000_000.0
    old = now - RETENTION_SECONDS - 1
    gone = _record(store, status="exited", exit_code=0, ended_at=old, background=True, reported=True)
    silent = _record(store, status="unknown", ended_at=None, heartbeat=old)
    recent = _record(store, status="exited", exit_code=0, ended_at=now - 60, reported=True)
    unseen = _record(store, status="exited", exit_code=1, ended_at=old, background=True, reported=False)
    running = _record(store, status="running", heartbeat=now)

    # A notice a day late is stale, so an unseen exit goes too.
    assert store.prune(now) == 3
    kept = {row["id"] for row in store.list()}
    assert kept == {recent["id"], running["id"]}
    assert store.get(unseen["id"]) is None
    assert store.get(gone["id"]) is None and store.get(silent["id"]) is None
