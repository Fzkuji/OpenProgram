"""``worker stop`` stops a worker that holds its lock but has no PID file yet."""
from __future__ import annotations

from openprogram.operations.worker import lifecycle
from openprogram.operations.worker import lock as worker_lock


class _HeldLock:
    def try_acquire(self) -> bool:
        return False

    def release(self) -> None:
        pass


class _FreeLock(_HeldLock):
    def try_acquire(self) -> bool:
        return True


def test_a_starting_worker_is_waited_for_and_stopped(monkeypatch) -> None:
    answers = iter([None, None, None, 4242])
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: next(answers))
    monkeypatch.setattr(worker_lock, "WorkerLock", _HeldLock)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: None)
    signalled: list[int] = []
    monkeypatch.setattr(lifecycle.os, "kill", lambda pid, sig: signalled.append(pid))
    monkeypatch.setattr(lifecycle, "_process_alive", lambda pid: False)
    assert lifecycle.stop_worker(prefer_service=False) == 0
    assert signalled == [4242]


def test_no_lock_holder_means_not_running(monkeypatch, capsys) -> None:
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: None)
    monkeypatch.setattr(worker_lock, "WorkerLock", _FreeLock)
    assert lifecycle.stop_worker(prefer_service=False) == 0
    assert "not running" in capsys.readouterr().out
