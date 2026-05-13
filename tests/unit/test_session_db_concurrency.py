"""SessionDB cross-process concurrency.

Multiple SessionDB instances on the same sqlite file simulate multi-
process deployments (gunicorn workers + channels worker + TUI all
attached). Each instance opens its own connection, so SQLite WAL's
file-level locking is what we're actually testing — not Python's
GIL.

What must hold under contention:
  - No "database is locked" leaks past _execute_write's retry layer
  - All writes from all processes land (no lost updates)
  - Readers see writes from other processes
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openprogram.agent.session_db import SessionDB


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.sqlite"


def test_two_instances_share_session(db_path: Path) -> None:
    """Process A creates + writes; process B reads. Most basic cross-
    process correctness — guarantees WAL is wired and rows are
    visible across connections."""
    a = SessionDB(db_path)
    b = SessionDB(db_path)

    a.create_session("c1", "main", title="from-a")
    a.append_message("c1", {"id": "m1", "role": "user", "content": "hi",
                              "timestamp": 1.0, "parent_id": None})

    sess = b.get_session("c1")
    assert sess is not None
    assert sess["title"] == "from-a"
    msgs = b.get_messages("c1")
    assert [m["id"] for m in msgs] == ["m1"]


def test_concurrent_writers_no_loss(db_path: Path) -> None:
    """8 worker threads each writing 50 messages to the same session.
    No "database is locked" errors should leak through; total row
    count must equal sum of writes."""
    n_workers = 8
    msgs_per_worker = 50
    db_setup = SessionDB(db_path)
    db_setup.create_session("c1", "main")

    def _worker(worker_id: int) -> None:
        # Each worker opens its own SessionDB, simulating a separate
        # process. Workers don't share the connection — that's the
        # interesting case for WAL-write contention.
        local = SessionDB(db_path)
        for i in range(msgs_per_worker):
            local.append_message("c1", {
                "id": f"w{worker_id}-m{i}",
                "role": "user",
                "content": f"from worker {worker_id} msg {i}",
                "timestamp": time.time() + i * 0.001,
                "parent_id": None,
            })

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        list(ex.map(_worker, range(n_workers)))

    final = SessionDB(db_path)
    rows = final.get_messages("c1")
    assert len(rows) == n_workers * msgs_per_worker
    # And every (worker, msg) pair is represented exactly once.
    expected = {f"w{w}-m{i}" for w in range(n_workers)
                for i in range(msgs_per_worker)}
    actual = {r["id"] for r in rows}
    assert actual == expected


def test_reader_sees_writer_progress(db_path: Path) -> None:
    """A long-running reader should see writes from another process
    appear without needing a fresh connection."""
    writer = SessionDB(db_path)
    reader = SessionDB(db_path)
    writer.create_session("c1", "main")

    seen_counts = []
    stop = threading.Event()

    def _read_loop():
        while not stop.is_set():
            seen_counts.append(len(reader.get_messages("c1")))
            time.sleep(0.005)

    t = threading.Thread(target=_read_loop, daemon=True)
    t.start()
    try:
        for i in range(20):
            writer.append_message("c1", {
                "id": f"m{i}", "role": "user", "content": "x",
                "timestamp": time.time(), "parent_id": None,
            })
            time.sleep(0.005)
        # Give the reader a couple more polling intervals after the
        # last append so it has a chance to observe message 20 before
        # we signal stop. Without this the reader thread sometimes
        # exits the loop between the 19th and 20th read iteration.
        time.sleep(0.05)
    finally:
        stop.set()
        t.join(timeout=1.0)

    # Reader observed an increasing count culminating at 20.
    assert max(seen_counts) == 20
    # And it wasn't stuck at 0 — proves WAL readers actually saw
    # cross-connection commits (regression on a misconfigured WAL or
    # cached snapshot would freeze the reader at len=0).
    assert seen_counts[-1] >= 15


def test_concurrent_set_head_serializes(db_path: Path) -> None:
    """N workers racing to set_head shouldn't deadlock or corrupt the
    sessions row. Last write wins is fine (not strictly ordered, but
    no exceptions / no lost rows)."""
    setup = SessionDB(db_path)
    setup.create_session("c1", "main")
    for i in range(10):
        setup.append_message("c1", {
            "id": f"m{i}", "role": "user", "content": "x",
            "timestamp": float(i), "parent_id": f"m{i-1}" if i else None,
        })

    def _worker(target_id: str) -> None:
        local = SessionDB(db_path)
        for _ in range(20):
            local.set_head("c1", target_id)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_worker, [f"m{i}" for i in range(8)]))

    # Whatever the final head is, it must be one of the targets we set
    # (rather than corrupted / NULL / wrong).
    final = SessionDB(db_path).get_session("c1")
    assert final["head_id"] in {f"m{i}" for i in range(8)}
