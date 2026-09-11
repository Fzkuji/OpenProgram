from __future__ import annotations

import multiprocessing
from pathlib import Path

from openprogram.store.session.migration import load_journal, update_journal_row
from openprogram.store.session.session_store import SessionStore


def _journal_worker(root: str, session_id: str, barrier) -> None:
    journal = load_journal(Path(root))
    barrier.wait()
    journal.setdefault("sessions", {})[session_id] = {
        "session_id": session_id, "stage": "copy", "source": session_id,
    }
    update_journal_row(Path(root), session_id, journal["sessions"][session_id])


def _location_worker(root: str, session_id: str, barrier) -> None:
    store = SessionStore(Path(root))
    barrier.wait()
    store._record_location(session_id, Path(root) / "projects" / session_id)


def _run_pair(target, root: Path, barrier):
    ctx = multiprocessing.get_context("spawn")
    processes = [ctx.Process(target=target, args=(str(root), sid, barrier)) for sid in ("a", "b")]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0


def test_concurrent_journal_updates_preserve_both_sessions(tmp_path):
    _run_pair(_journal_worker, tmp_path / "sessions", multiprocessing.get_context("spawn").Barrier(2))
    journal = load_journal(tmp_path / "sessions")
    assert set(journal["sessions"]) == {"a", "b"}


def test_concurrent_location_updates_preserve_both_sessions(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    _run_pair(_location_worker, root, multiprocessing.get_context("spawn").Barrier(2))
    locations = SessionStore(root)._load_locations()
    assert set(locations) == {"a", "b"}
