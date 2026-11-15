from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

from openprogram.host._platform import asyncio as platform_asyncio
from openprogram.host._platform import filesystem
from openprogram.host._platform import process_ownership
from openprogram.host._platform import processes
from openprogram.host._platform import terminal


def test_process_command_line_uses_cim(monkeypatch) -> None:
    scripts: list[str] = []
    monkeypatch.setattr(sys, "platform", "win32")

    def fake(script: str, *, timeout: float = 5.0) -> str:
        del timeout
        scripts.append(script)
        return "  python.exe -m openprogram worker run  "

    monkeypatch.setattr(terminal, "_windows_powershell", fake)

    assert processes.process_command_line(42) == (
        "python.exe -m openprogram worker run"
    )
    assert "Get-CimInstance Win32_Process" in scripts[0]
    assert "ProcessId = 42" in scripts[0]


def test_process_command_line_reads_the_kernel_before_cim(monkeypatch) -> None:
    # A new PowerShell per query can outlast its timeout on a fresh or busy
    # machine; the kernel answer needs none.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(processes, "_windows_command_line",
                        lambda pid: " python.exe -m openprogram worker run ")

    def no_powershell(script: str, *, timeout: float = 5.0) -> str:
        raise AssertionError("CIM must not run when the kernel answered")

    monkeypatch.setattr(terminal, "_windows_powershell", no_powershell)
    assert processes.process_command_line(42) == "python.exe -m openprogram worker run"


def test_process_command_line_falls_back_to_cim(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(processes, "_windows_command_line", lambda pid: None)
    monkeypatch.setattr(terminal, "_windows_powershell",
                        lambda script, *, timeout=5.0: "python.exe x")
    assert processes.process_command_line(42) == "python.exe x"


def test_powershell_failure_is_a_safe_empty_result(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: "powershell.exe")
    # On Windows the kernel answers for our own process; this test is about
    # the CIM fallback, so the kernel must decline first.
    monkeypatch.setattr(processes, "_windows_command_line", lambda pid: None)

    def fail(*args, **kwargs):
        del args, kwargs
        raise subprocess.TimeoutExpired("powershell.exe", 5)

    monkeypatch.setattr(subprocess, "run", fail)
    assert processes.process_command_line(os.getpid()) == ""


def test_windows_pids_on_port_parses_only_matching_listeners(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

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

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(process_ownership, "no_window_creation_flags", lambda: 0x08000000)

    assert processes.pids_on_port(18100) == [123]
    assert seen["creationflags"] == 0x08000000


def test_windows_private_directory_preserves_inherited_acl(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    filesystem.restrict_directory_to_user(tmp_path)

    assert calls == []


def test_asyncio_handler_ignores_only_windows_proactor_peer_reset(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
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
    platform_asyncio.install_asyncio_exception_handler(loop)
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
    monkeypatch.setattr(sys, "platform", "win32")
    delegated: list[tuple[object, dict]] = []

    class Loop:
        handler = None

        def get_exception_handler(self):
            return lambda loop, context: delegated.append((loop, context))

        def set_exception_handler(self, handler):
            self.handler = handler

    loop = Loop()
    platform_asyncio.install_asyncio_exception_handler(loop)
    context = {"exception": OSError("not a proactor reset")}

    loop.handler(loop, context)

    assert delegated == [(loop, context)]


def test_detached_worker_never_creates_a_windows_console(
    tmp_path, monkeypatch
) -> None:
    from openprogram.operations.worker import lifecycle

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
    from openprogram.host._platform import processes
    from openprogram.operations.worker import lifecycle

    log_path = tmp_path / "worker.log"

    class Process:
        pid = 4343

    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: None)
    monkeypatch.setattr(lifecycle.paths, "log_path", lambda: log_path)
    monkeypatch.setattr(lifecycle, "_detached_worker_command", lambda: ["python"])
    monkeypatch.setattr(lifecycle.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    killed: list[int] = []
    monkeypatch.setattr(processes, "kill_process_tree", lambda pid: killed.append(pid) or True)

    def fail_to_record(_pid: int) -> None:
        raise OSError("disk full")

    assert lifecycle.spawn_detached(
        prefer_service=False, on_spawn=fail_to_record
    ) == 1
    assert killed == [4343]
    assert "failed to record spawned worker: OSError: disk full" in capsys.readouterr().out


# What a Chinese Windows prints: netstat's header and the schtasks and
# PowerShell errors are in the OEM code page (cp936), which is not UTF-8.
_NETSTAT_ZH = (
    "\r\n活动连接\r\n\r\n  协议  本地地址          外部地址        状态           PID\r\n"
    "  TCP    127.0.0.1:18100    0.0.0.0:0       LISTENING       4321\r\n"
)


def _console_tool_writes(monkeypatch, *, stdout: str = "", stderr: str = "", code: int = 0):
    """Replace the next console tool with a real child writing cp936 bytes."""

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(processes, "_oem_codec", lambda: "cp936")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    real_run = subprocess.run
    child = (
        "import sys;"
        f"sys.stdout.buffer.write({stdout.encode('cp936')!r});"
        f"sys.stderr.buffer.write({stderr.encode('cp936')!r});"
        f"sys.exit({code})"
    )

    def run(argv, **options):
        del argv
        # Its own console, so the OEM code page is the one it writes in.
        assert options.pop("creationflags") == 0x08000000
        return real_run([sys.executable, "-I", "-c", child], **options)

    monkeypatch.setattr(subprocess, "run", run)


def test_windows_pids_on_port_reads_netstat_in_the_console_code_page(monkeypatch) -> None:
    _console_tool_writes(monkeypatch, stdout=_NETSTAT_ZH)

    assert processes.pids_on_port(18100) == [4321]


def test_console_text_reads_the_console_code_page(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(processes, "_oem_codec", lambda: "cp936")

    # These four bytes are also valid UTF-8, for two unrelated characters.
    assert "状态".encode("cp936").decode("utf-8") != "状态"
    assert processes.console_text("状态".encode("cp936")) == "状态"
    assert processes.console_text(b"\x81") == "\ufffd"
    assert processes.console_text(None) == ""
    assert processes.console_text("already text") == "already text"


def test_windows_schtasks_failure_is_reported_in_the_console_code_page(
    tmp_path, monkeypatch
) -> None:
    import pytest

    from openprogram.operations.self_update.delivery import controller, launcher_windows

    _console_tool_writes(monkeypatch, stderr="错误: 拒绝访问。\r\n", code=1)

    with pytest.raises(controller.ControllerLaunchError, match="拒绝访问"):
        launcher_windows.submit_controller(
            "ai.test", ["C:\\py.exe"], {}, tmp_path / "supervisor.log"
        )


def test_windows_service_powershell_error_is_read_in_the_console_code_page(
    monkeypatch,
) -> None:
    from openprogram.operations.worker.services import windows

    _console_tool_writes(monkeypatch, stderr="找不到任务。\r\n", code=1)
    monkeypatch.setattr(windows.shutil, "which", lambda name: "powershell.exe")

    assert windows._powershell("Get-ScheduledTask") == (1, "找不到任务。")
