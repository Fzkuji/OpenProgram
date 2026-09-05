from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

from openprogram import _compat


def test_process_command_line_uses_cim(monkeypatch) -> None:
    scripts: list[str] = []
    monkeypatch.setattr(_compat._sys, "platform", "win32")

    def fake(script: str, *, timeout: float = 5.0) -> str:
        del timeout
        scripts.append(script)
        return "  python.exe -m openprogram worker run  "

    monkeypatch.setattr(_compat, "_windows_powershell", fake)

    assert _compat.process_command_line(42) == (
        "python.exe -m openprogram worker run"
    )
    assert "Get-CimInstance Win32_Process" in scripts[0]
    assert "ProcessId = 42" in scripts[0]


def test_process_ids_by_name_validates_and_parses(monkeypatch) -> None:
    scripts: list[str] = []
    monkeypatch.setattr(_compat._sys, "platform", "win32")

    def fake(script: str, *, timeout: float = 5.0) -> str:
        del timeout
        scripts.append(script)
        return "12\nnot-a-pid\n34\n"

    monkeypatch.setattr(_compat, "_windows_powershell", fake)

    assert _compat.process_ids_by_name(
        ["chrome.exe", "chrome.exe", "bad'name"]
    ) == [12, 34]
    assert "Name = 'chrome.exe'" in scripts[0]
    assert "bad'name" not in scripts[0]


def test_powershell_failure_is_a_safe_empty_result(monkeypatch) -> None:
    monkeypatch.setattr(_compat._sys, "platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: "powershell.exe")

    def fail(*args, **kwargs):
        del args, kwargs
        raise subprocess.TimeoutExpired("powershell.exe", 5)

    monkeypatch.setattr(_compat._subprocess, "run", fail)
    assert _compat.process_command_line(os.getpid()) == ""


def test_windows_pids_on_port_parses_only_matching_listeners(monkeypatch) -> None:
    monkeypatch.setattr(_compat._sys, "platform", "win32")

    class Result:
        stdout = """
          TCP    127.0.0.1:18100    0.0.0.0:0       LISTENING       123
          TCP    127.0.0.1:18101    0.0.0.0:0       LISTENING       456
          TCP    127.0.0.1:18100    127.0.0.1:5000  ESTABLISHED     789
        """

    seen: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return Result()

    monkeypatch.setattr(_compat._subprocess, "run", fake_run)
    monkeypatch.setattr(_compat, "no_window_creation_flags", lambda: 0x08000000)

    assert _compat.pids_on_port(18100) == [123]
    assert seen["creationflags"] == 0x08000000


def test_kill_processes_matching_normalizes_windows_paths(monkeypatch) -> None:
    monkeypatch.setattr(_compat._sys, "platform", "win32")
    monkeypatch.setattr(_compat, "process_ids_by_name", lambda names: [11, 22])
    monkeypatch.setattr(
        _compat,
        "process_command_line",
        lambda pid: (
            r"chrome.exe --user-data-dir=C:\Users\me\.openprogram\chrome-profile"
            if pid == 22
            else "chrome.exe --other-profile"
        ),
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(command)

    monkeypatch.setattr(_compat._subprocess, "run", fake_run)

    _compat.kill_processes_matching(
        ["chrome.exe"], "openprogram/chrome-profile"
    )

    assert calls == [["taskkill", "/F", "/T", "/PID", "22"]]


def test_windows_private_directory_preserves_inherited_acl(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(_compat._sys, "platform", "win32")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        _compat._subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    _compat.restrict_directory_to_user(tmp_path)

    assert calls == []


def test_asyncio_handler_ignores_only_windows_proactor_peer_reset(monkeypatch) -> None:
    monkeypatch.setattr(_compat._sys, "platform", "win32")
    default_contexts: list[dict] = []

    class Loop:
        handler = None

        def get_exception_handler(self):
            return None

        def set_exception_handler(self, handler):
            self.handler = handler

        def default_exception_handler(self, context):
            default_contexts.append(context)

    class ProactorTransport:
        __module__ = "asyncio.proactor_events"

        def _call_connection_lost(self):
            return None

    loop = Loop()
    _compat.install_asyncio_exception_handler(loop)
    reset = ConnectionResetError("peer reset")
    reset.winerror = 10054
    benign = {
        "exception": reset,
        "handle": SimpleNamespace(
            _callback=ProactorTransport()._call_connection_lost,
        ),
    }

    loop.handler(loop, benign)

    assert default_contexts == []

    unexpected = {"exception": RuntimeError("boom")}
    loop.handler(loop, unexpected)
    assert default_contexts == [unexpected]


def test_asyncio_handler_preserves_existing_handler(monkeypatch) -> None:
    monkeypatch.setattr(_compat._sys, "platform", "win32")
    delegated: list[tuple[object, dict]] = []

    class Loop:
        handler = None

        def get_exception_handler(self):
            return lambda loop, context: delegated.append((loop, context))

        def set_exception_handler(self, handler):
            self.handler = handler

    loop = Loop()
    _compat.install_asyncio_exception_handler(loop)
    context = {"exception": OSError("not a proactor reset")}

    loop.handler(loop, context)

    assert delegated == [(loop, context)]


def test_detached_worker_never_creates_a_windows_console(
    tmp_path, monkeypatch
) -> None:
    from openprogram.worker import lifecycle

    log_path = tmp_path / "worker.log"
    calls = iter([None, 4242])
    seen: dict[str, object] = {}

    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return None

    def fake_popen(command, **kwargs):
        seen.update(command=command, **kwargs)
        return Process()

    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: next(calls))
    monkeypatch.setattr(lifecycle, "read_worker_port", lambda: 18100)
    monkeypatch.setattr(lifecycle.paths, "log_path", lambda: log_path)
    monkeypatch.setattr(lifecycle, "_detached_worker_command", lambda: ["python.exe"])
    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lifecycle, "no_window_creation_flags", lambda: 0x08000000)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _seconds: None)
    spawned: list[int] = []

    assert lifecycle.spawn_detached(on_spawn=spawned.append) == 0
    assert spawned == [4242]
    assert seen["creationflags"] == 0x08000000
    assert seen["stdin"] is subprocess.DEVNULL


def test_detached_worker_is_killed_when_spawn_recording_fails(
    tmp_path, monkeypatch, capsys
) -> None:
    from openprogram import _compat
    from openprogram.worker import lifecycle

    log_path = tmp_path / "worker.log"

    class Process:
        pid = 4343

    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: None)
    monkeypatch.setattr(lifecycle.paths, "log_path", lambda: log_path)
    monkeypatch.setattr(lifecycle, "_detached_worker_command", lambda: ["python"])
    monkeypatch.setattr(lifecycle.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    killed: list[int] = []
    monkeypatch.setattr(_compat, "kill_process_tree", lambda pid: killed.append(pid) or True)

    def fail_to_record(_pid: int) -> None:
        raise OSError("disk full")

    assert lifecycle.spawn_detached(
        prefer_service=False, on_spawn=fail_to_record
    ) == 1
    assert killed == [4343]
    assert "failed to record spawned worker: OSError: disk full" in capsys.readouterr().out
