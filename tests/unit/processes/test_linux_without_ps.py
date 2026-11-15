"""A shell command runs on Linux with no ``ps`` installed (slim images)."""
from __future__ import annotations

import os
import time

from openprogram.host._platform import processes as platform_processes
from openprogram.host.processes import group_guard, store


def _stat(pid, state, pgrp, comm="sh", session=None):
    # Real layout: pid (comm) state ppid pgrp session ...
    session = pgrp if session is None else session
    return f"{pid} ({comm}) {state} 1 {pgrp} {session} 0 -1 0 0\n"


def _proc(tmp_path, rows):
    (tmp_path / "self").mkdir(parents=True)
    (tmp_path / "net").mkdir()  # a non-numeric entry is skipped
    for pid, text in rows.items():
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "stat").write_text(text)
    return str(tmp_path)


def _no_ps(*_args, **_kwargs):
    raise FileNotFoundError("ps")


def test_group_members_are_read_from_proc_without_ps(tmp_path, monkeypatch):
    me = os.getpid()
    monkeypatch.setattr(group_guard.subprocess, "Popen", _no_ps)
    alive = _proc(tmp_path / "a", {
        me: _stat(me, "S", me),
        10: _stat(10, "R", me, comm="my prog) S 1 99"),  # ')' inside comm
        11: _stat(11, "S", 99),
    })
    assert group_guard._other_members(alive) is True

    only_zombies = _proc(tmp_path / "b", {
        me: _stat(me, "S", me),
        10: _stat(10, "Z", me),
        11: _stat(11, "S", 99),
    })
    assert group_guard._other_members(only_zombies) is False

    # A job-control job: its own group, still the leader's session.
    other_group = _proc(tmp_path / "c", {
        me: _stat(me, "S", me),
        12: _stat(12, "S", 12, session=me),
        13: _stat(13, "S", 99),
    })
    assert group_guard._other_members(other_group) is False
    assert group_guard._other_members(other_group, session=True) is True
    assert group_guard._other_members(only_zombies, session=True) is False


def test_process_identity_on_linux_reads_proc_not_ps(monkeypatch):
    monkeypatch.setattr(store.sys, "platform", "linux")
    monkeypatch.setattr(store.subprocess, "run", _no_ps)
    monkeypatch.setattr(store, "_proc_lstart", lambda pid: f"lstart of {pid}")
    assert store.process_identity(42) == "lstart of 42"


def test_linux_identity_keeps_the_format_ps_recorded(tmp_path, monkeypatch):
    # Identities written while Linux still ran `ps -o lstart=` must keep
    # matching: same seconds (btime + starttime ticks // CLK_TCK), ctime layout.
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        monkeypatch.setattr(store.os, "sysconf", lambda name: 100)
        (tmp_path / "stat").write_text("cpu  1 2 3\nbtime 1793491200\nprocesses 9\n")
        (tmp_path / "7").mkdir()
        fields = ["S"] + ["0"] * 18 + ["12399"] + ["0"] * 5  # starttime is field 22
        (tmp_path / "7" / "stat").write_text("7 (a) b) " + " ".join(fields) + "\n")
        # 1793491200 is Sun Nov  1 00:00:00 2026 UTC; 12399 ticks is 123 s.
        assert store._proc_lstart(7, str(tmp_path)) == "Sun Nov  1 00:02:03 2026"
        assert store._proc_lstart(8, str(tmp_path)) is None
    finally:
        monkeypatch.undo()
        time.tzset()


def test_command_line_on_linux_never_falls_back_to_ps(monkeypatch):
    monkeypatch.setattr(platform_processes._sys, "platform", "linux")
    monkeypatch.setattr(platform_processes._subprocess, "run", _no_ps)
    assert platform_processes.process_command_line(2**22 + 12345) == ""

