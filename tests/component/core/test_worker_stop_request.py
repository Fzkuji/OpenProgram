"""``worker stop`` on Windows asks the worker to shut down instead of ending it.

``os.kill(pid, SIGTERM)`` is ``TerminateProcess`` there. Not run on Windows:
the platform name is injected and the request file is a real file.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from openprogram.operations.worker import lifecycle, paths, runner


@pytest.fixture
def request_file(tmp_path, monkeypatch):
    target = tmp_path / "worker.stop"
    monkeypatch.setattr(paths, "stop_request_path", lambda: target)
    return target


def _no_kill(pid, sig):
    raise AssertionError("os.kill on Windows is TerminateProcess")


def test_windows_stop_asks_through_the_request_file(monkeypatch, request_file) -> None:
    monkeypatch.setattr(lifecycle.sys, "platform", "win32")
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: 4242)
    monkeypatch.setattr(lifecycle.os, "kill", _no_kill)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: None)
    seen: list[str] = []

    def alive(pid):
        if not request_file.exists():
            return True  # the liveness check before the request is written
        seen.append(request_file.read_text(encoding="utf-8").strip())
        return len(seen) < 3  # the worker reads the request and exits

    monkeypatch.setattr(lifecycle, "_process_alive", alive)

    assert lifecycle.stop_worker(prefer_service=False) == 0
    assert seen[0] == "4242"
    assert not request_file.exists()
    assert not list(request_file.parent.glob("*.tmp"))


def test_windows_stop_kills_the_tree_of_a_worker_that_does_not_answer(
    monkeypatch, request_file
) -> None:
    monkeypatch.setattr(lifecycle.sys, "platform", "win32")
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: 4242)
    monkeypatch.setattr(lifecycle.os, "kill", _no_kill)
    # Sleeping moves the clock, so the five-second wait passes at once.
    real_time, slept = lifecycle.time.time, [0.0]
    monkeypatch.setattr(lifecycle.time, "time", lambda: real_time() + slept[0])
    monkeypatch.setattr(
        lifecycle.time, "sleep", lambda s: slept.__setitem__(0, slept[0] + s)
    )
    killed: list[int] = []
    monkeypatch.setattr(lifecycle, "_process_alive", lambda pid: not killed)
    monkeypatch.setattr(
        "openprogram.host._platform.processes.kill_process_tree",
        lambda pid: killed.append(pid) or True,
    )

    assert lifecycle.stop_worker(prefer_service=False) == 0
    assert killed == [4242]
    assert not request_file.exists()


def test_windows_stop_of_a_gone_worker_writes_no_request(monkeypatch, request_file, capsys) -> None:
    monkeypatch.setattr(lifecycle.sys, "platform", "win32")
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: 4242)
    monkeypatch.setattr(lifecycle.os, "kill", _no_kill)
    monkeypatch.setattr(lifecycle, "_process_alive", lambda pid: False)

    assert lifecycle.stop_worker(prefer_service=False) == 0
    assert "already gone" in capsys.readouterr().out
    assert not request_file.exists()


def test_the_worker_leaves_its_wait_when_a_stop_names_it(request_file) -> None:
    naps = []

    def nap(seconds):
        naps.append(seconds)
        if len(naps) == 3:
            request_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
        assert len(naps) < 10, "the request was not read"

    with pytest.raises(KeyboardInterrupt):
        runner._wait_for_stop(sleep=nap)
    assert len(naps) == 3
    assert max(naps) <= 0.5  # the shutdown has to fit in stop_worker's five seconds


def test_a_request_for_another_process_is_not_this_workers(request_file) -> None:
    request_file.write_text(f"{os.getpid() + 1}\n", encoding="utf-8")
    assert not lifecycle.stop_requested()
    lifecycle.clear_stop_request()
    assert not request_file.exists()


def test_a_running_process_reads_a_stop_asked_from_another(request_file) -> None:
    """End to end with a real child. On Windows the request goes through
    ``request_stop`` itself; elsewhere that is a signal, so the file is
    written here."""
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(
            """
            import sys
            from pathlib import Path
            from openprogram.operations.worker import paths, runner
            target = Path(sys.argv[1])
            paths.stop_request_path = lambda: target
            print("ready", flush=True)
            try:
                runner._wait_for_stop()
            except KeyboardInterrupt:
                print("stopping", flush=True)
            """
        ), str(request_file)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        if sys.platform == "win32":
            lifecycle.request_stop(child.pid)
        else:
            request_file.write_text(f"{child.pid}\n", encoding="utf-8")
        output, _ = child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert output.strip() == "stopping"
    assert child.returncode == 0
