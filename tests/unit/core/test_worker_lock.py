from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(os.name != "nt", reason="covers Windows msvcrt lock semantics")
def test_same_process_lock_probe_reports_the_holder(tmp_path, monkeypatch) -> None:
    from openprogram.worker import paths
    from openprogram.worker.lock import WorkerLock, is_held_by

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
