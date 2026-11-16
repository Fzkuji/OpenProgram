"""Recognising this profile's worker, and the port it settles on.

On Windows no outside process recognised the worker: the lock byte was the
PID byte, which msvcrt makes unreadable, so the holder's PID could not be read
and the App never got its sign-in URL. The lock is the only proof of the
worker; a live process named in ``worker.pid`` is not. Separately, a fixed
port 18100 left a second account, or a user with another program on 18100,
without a worker.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from openprogram.operations.worker import lifecycle, runner
from openprogram.operations.worker import ports
from openprogram.interfaces.cli.commands.services import web as web_command


# ---- recognising the worker ------------------------------------------------

def test_the_holder_pid_reads_while_another_handle_holds_the_lock(tmp_path, monkeypatch):
    from openprogram.operations.worker import lock as worker_lock

    lock_file = tmp_path / "worker.lock"
    monkeypatch.setattr(worker_lock, "lock_path", lambda: lock_file)
    monkeypatch.setattr("openprogram.state.store.session.files.session_log.claim_main_writer", lambda: None)
    monkeypatch.setattr("openprogram.state.store.session.files.session_log.release_main_writer", lambda: None)
    held = worker_lock.WorkerLock()
    assert held.try_acquire()
    try:
        assert worker_lock.read_holder_pid() == held.holder_pid
        probe = worker_lock.WorkerLock()
        assert not probe.try_acquire()
        assert probe.holder_pid == held.holder_pid
        assert worker_lock.is_held_by(held.holder_pid)
    finally:
        held.release()


def _windows_locking(monkeypatch):
    """host/_platform/locking.py as Windows loads it, over a recording msvcrt."""
    calls: list[tuple[int, int, int]] = []
    msvcrt = types.SimpleNamespace(LK_UNLCK=0, LK_LOCK=1, LK_NBLCK=2)

    def locking(fd, mode, nbytes):
        import os

        calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), nbytes))

    msvcrt.locking = locking
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setitem(sys.modules, "fcntl", None)  # ImportError, as on Windows
    source = Path(__file__).resolve().parents[3] / "openprogram/host/_platform/locking.py"
    spec = importlib.util.spec_from_file_location("windows_locking_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def test_windows_locks_a_byte_past_the_data_and_keeps_the_position(tmp_path, monkeypatch):
    module, calls = _windows_locking(monkeypatch)
    path = tmp_path / "worker.lock"
    with open(path, "a+", encoding="utf-8") as handle:
        handle.write("2448\n")
        handle.flush()
        import os

        os.lseek(handle.fileno(), 3, os.SEEK_SET)
        module.flock(handle.fileno(), module.LOCK_EX | module.LOCK_NB)
        assert os.lseek(handle.fileno(), 0, os.SEEK_CUR) == 3
        module.flock(handle.fileno(), module.LOCK_UN)
    assert [offset for _mode, offset, _n in calls] == [module._LOCK_OFFSET] * 2
    # The PID the worker writes at the start of the file is never locked, so
    # `web auth-url`, `worker status` and `worker stop` can read it.
    assert module._LOCK_OFFSET > 1 << 20


# ---- the port ----------------------------------------------------------------

@pytest.fixture()
def state(tmp_path, monkeypatch):
    record = tmp_path / "web-port"
    monkeypatch.setattr(lifecycle.paths, "web_port_record_path", lambda: record)
    monkeypatch.delenv("OPENPROGRAM_WEB_PORT", raising=False)
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {})
    taken: set[int] = set()
    monkeypatch.setattr(ports, "port_bindable", lambda port, host="127.0.0.1": port not in taken)
    return types.SimpleNamespace(record=record, taken=taken)


def test_a_first_start_takes_18100_and_keeps_it(state):
    assert lifecycle.claim_worker_port() == (18100, None)
    assert state.record.read_text() == "18100\n"
    assert lifecycle.resolve_worker_port() == 18100


def test_a_second_account_or_a_program_on_18100_gets_the_next_free_port(state):
    state.taken.update({18100, 18101})
    assert lifecycle.claim_worker_port() == (18102, None)
    state.taken.clear()
    # The choice is this profile's from now on, even once 18100 is free again.
    assert lifecycle.claim_worker_port() == (18102, None)


def test_a_chosen_port_taken_later_moves_and_the_record_follows(state):
    state.record.write_text("18100\n")
    state.taken.add(18100)
    assert lifecycle.claim_worker_port(held=18100) == (18101, None)
    assert lifecycle.recorded_web_port() == 18101


def test_a_port_the_user_set_never_moves(state, monkeypatch):
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"ui": {"web_port": 18555}})
    state.taken.add(18555)
    assert lifecycle.claim_worker_port(held=18555) == (18555, "ui.web_port")
    monkeypatch.setenv("OPENPROGRAM_WEB_PORT", "18777")
    assert lifecycle.claim_worker_port() == (18777, "OPENPROGRAM_WEB_PORT")
    assert not state.record.exists()


def test_the_first_writer_of_the_record_wins(state):
    state.record.write_text("18104\n")  # the App or another CLI got there first
    assert lifecycle._record_web_port(18100, replace=False) == 18104


def test_the_worker_moves_off_a_chosen_port_and_refuses_a_set_one(state, monkeypatch, capsys):
    monkeypatch.setattr(ports, "describe_port_owner", lambda _port: ports.PortOwner(
        pids=[4242], kind="other", detail="PID 4242: other.exe"))
    state.record.write_text("18100\n")
    state.taken.add(18100)
    assert runner._settle_web_port() == 18101
    assert "PID 4242: other.exe" in capsys.readouterr().out

    monkeypatch.setenv("OPENPROGRAM_WEB_PORT", "18101")
    state.taken.add(18101)
    assert runner._settle_web_port() is None
    out = capsys.readouterr().out
    assert "set by OPENPROGRAM_WEB_PORT" in out and "PID 4242" in out


def test_a_pid_file_without_the_lock_is_not_the_worker(tmp_path, monkeypatch):
    import os

    from openprogram.operations.worker import lock as worker_lock

    monkeypatch.setattr(worker_lock, "lock_path", lambda: tmp_path / "worker.lock")
    monkeypatch.setattr(lifecycle, "read_holder_pid", lambda: None)
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text(f"{os.getpid()}\n0\n", encoding="utf-8")
    monkeypatch.setattr(lifecycle.paths, "pid_path", lambda: pid_file)
    assert lifecycle.current_worker_pid() is None


def test_a_listener_without_this_profiles_worker_is_never_reported_as_ours(monkeypatch):
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: None)
    assert lifecycle.read_worker_port() is None
    assert lifecycle.find_running_webui() == (None, None, "none")


def test_auth_url_names_the_holder_of_a_port_that_is_not_ours(monkeypatch, capsys):
    monkeypatch.setattr(lifecycle, "read_worker_port", lambda: None)
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: None)
    monkeypatch.setattr(web_command, "_port_in_use", lambda port: port == 18100)
    monkeypatch.setattr(web_command, "port_owner_hint", lambda _port: "  Held by — PID 4242: other.exe")

    assert web_command._cmd_web_auth_url("http://127.0.0.1:18100") == web_command.EXIT_PORT_HELD
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "port 18100 is in use by PID 4242: other.exe\n"

    assert web_command._cmd_web_auth_url("http://127.0.0.1:18101") == 1


def test_auth_url_does_not_call_a_starting_worker_a_holder(monkeypatch, capsys):
    # The worker listens a moment before it names its port in worker.port.
    monkeypatch.setattr(lifecycle, "read_worker_port", lambda: None)
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: 4242)
    monkeypatch.setattr(web_command, "_port_in_use", lambda port: True)
    assert web_command._cmd_web_auth_url("http://127.0.0.1:18100") == 1
    assert "no active OpenProgram Web server" in capsys.readouterr().err


def test_the_bind_probe_sees_a_live_listener(tmp_path):
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert not ports.port_bindable(port)
    assert ports.port_bindable(port)
