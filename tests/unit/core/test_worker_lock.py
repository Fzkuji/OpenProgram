from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(os.name != "nt", reason="covers Windows msvcrt lock semantics")
def test_same_process_lock_probe_reports_the_holder(tmp_path, monkeypatch) -> None:
    from openprogram.host.worker import paths
    from openprogram.host.worker.lock import WorkerLock, is_held_by

    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    lock = WorkerLock()
    assert lock.try_acquire() is True
    try:
        probe = WorkerLock()
        assert probe.try_acquire() is False
        assert probe.holder_pid == os.getpid()
        assert is_held_by(os.getpid()) is True
    finally:
        lock.release()

    assert is_held_by(os.getpid()) is False


def test_asking_who_holds_the_lock_does_not_touch_the_log_writer(tmp_path, monkeypatch) -> None:
    """A probe of a free lock once took and gave back the main-log claim, so
    a process that asked wrote its next lines to a file of its own."""
    from openprogram.host.worker import lock as worker_lock
    from openprogram.state.store.session.files import session_log

    monkeypatch.setattr(worker_lock, "lock_path", lambda: tmp_path / "worker.lock")
    for claimed in (True, False):
        (session_log.claim_main_writer if claimed else session_log.release_main_writer)()
        assert worker_lock.is_held_by(os.getpid()) is False
        assert session_log.is_main_writer() is claimed
        assert worker_lock.held_in_this_process(tmp_path / "worker.lock") is False
    assert (tmp_path / "worker.lock").read_text() == "", "a probe writes no PID"

    held = worker_lock.WorkerLock()
    assert held.try_acquire() and session_log.is_main_writer()
    held.release()
    assert not session_log.is_main_writer()
