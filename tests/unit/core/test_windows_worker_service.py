from __future__ import annotations

from pathlib import Path

from openprogram.operations.worker.services import windows


def test_windows_service_launcher_preserves_runtime_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        "openprogram.operations.worker.lifecycle._detached_worker_command",
        lambda: [r"C:\Program Files\OpenProgram\python.exe", "-I", "-B", "-u",
                 "-m", "openprogram", "worker", "run"],
    )

    script = windows._build_script()
    assert '"C:\\Program Files\\OpenProgram\\python.exe" -I -B -u' in script
    assert "openprogram worker run" in script
    assert "2>&1" in script


def test_windows_service_install_writes_and_registers_launcher(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = tmp_path / windows.SCRIPT_NAME
    log = tmp_path / "worker.log"
    calls: list[str] = []

    monkeypatch.setattr(windows, "_script_path", lambda: launcher)
    monkeypatch.setattr(windows.worker_paths, "log_path", lambda: log)
    monkeypatch.setattr(
        "openprogram.operations.worker.lifecycle.current_worker_pid", lambda: None
    )
    monkeypatch.setattr(windows, "_powershell", lambda script: calls.append(script) or (0, ""))
    monkeypatch.setattr("openprogram.host._platform.filesystem.restrict_to_user", lambda path: None)

    assert windows.install() == 0
    assert launcher.is_file()
    assert "worker run" in launcher.read_text(encoding="utf-8")
    assert "Register-ScheduledTask" in calls[0]
    assert "RestartCount 3" in calls[0]
    assert "Start-ScheduledTask" in calls[0]


def test_windows_service_uninstall_removes_launcher(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = tmp_path / windows.SCRIPT_NAME
    launcher.write_text("stale", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(windows, "_script_path", lambda: launcher)
    monkeypatch.setattr(windows, "_powershell", lambda script: calls.append(script) or (0, ""))

    assert windows.uninstall() == 0
    assert not launcher.exists()
    assert "Unregister-ScheduledTask" in calls[0]


def test_windows_service_status_skips_powershell_without_launcher(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    launcher = tmp_path / windows.SCRIPT_NAME
    monkeypatch.setattr(windows, "_script_path", lambda: launcher)
    monkeypatch.setattr(
        windows,
        "_powershell",
        lambda _script: (_ for _ in ()).throw(AssertionError("unexpected query")),
    )

    assert windows.status() == 0
    assert "installed: no" in capsys.readouterr().out


def _installed(tmp_path: Path, monkeypatch, *, worker_pid=None):
    """An installed task whose PowerShell and worker calls are recorded."""
    launcher = tmp_path / windows.SCRIPT_NAME
    launcher.write_text("stale launcher", encoding="utf-8")
    calls: list[str] = []
    running = [worker_pid]

    def stop_worker(*, prefer_service=True):
        assert prefer_service is False, "must not dispatch back to the service"
        calls.append("stop_worker")
        running[0] = None
        return 0

    monkeypatch.setattr(windows, "_script_path", lambda: launcher)
    monkeypatch.setattr(windows.worker_paths, "log_path", lambda: tmp_path / "worker.log")
    monkeypatch.setattr(windows, "_powershell", lambda script: calls.append(script) or (0, ""))
    monkeypatch.setattr(
        "openprogram.operations.worker.lifecycle.current_worker_pid", lambda: running[0]
    )
    monkeypatch.setattr("openprogram.operations.worker.lifecycle.stop_worker", stop_worker)
    monkeypatch.setattr("openprogram.host._platform.filesystem.restrict_to_user", lambda path: None)
    return launcher, calls


def test_windows_service_claims_no_command_without_an_installed_task(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(windows, "_script_path", lambda: tmp_path / windows.SCRIPT_NAME)

    assert windows.start_if_installed() is None
    assert windows.stop_if_installed() is None
    assert windows.restart_if_installed() is None


def test_windows_stop_asks_the_worker_then_ends_the_task_instance(
    tmp_path: Path, monkeypatch
) -> None:
    # Killing the worker alone leaves a failed run for the retry policy.
    _launcher, calls = _installed(tmp_path, monkeypatch, worker_pid=4242)

    assert windows.stop_if_installed() == 0
    assert calls[0] == "stop_worker"
    assert "Stop-ScheduledTask" in calls[1]
    assert "Register-ScheduledTask" not in "".join(calls)


def test_windows_start_rewrites_the_launcher_for_this_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    launcher, calls = _installed(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openprogram.operations.worker.lifecycle._detached_worker_command",
        lambda: [r"C:\OpenProgram\runtime\2.0.0\python.exe", "-m", "openprogram", "worker", "run"],
    )

    assert windows.start_if_installed() == 0
    assert r"runtime\2.0.0\python.exe" in launcher.read_text(encoding="utf-8")
    assert "Start-ScheduledTask" in calls[-1]


def test_windows_restart_stops_the_running_worker_before_it_registers(
    tmp_path: Path, monkeypatch
) -> None:
    _launcher, calls = _installed(tmp_path, monkeypatch, worker_pid=4242)

    assert windows.restart_if_installed() == 0
    assert calls[0] == "stop_worker"
    assert "Stop-ScheduledTask" in calls[1]
    assert "Register-ScheduledTask" in calls[2]


def test_lifecycle_stop_goes_through_the_installed_windows_task(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.operations.worker import services

    _launcher, calls = _installed(tmp_path, monkeypatch, worker_pid=4242)
    monkeypatch.setattr(
        "openprogram.host._platform.environment.worker_service_backend", lambda: "windows"
    )

    assert services.stop_if_installed() == 0
    assert any("Stop-ScheduledTask" in call for call in calls)


def test_a_named_profile_has_a_task_of_its_own(tmp_path: Path, monkeypatch) -> None:
    _launcher, calls = _installed(tmp_path, monkeypatch)
    monkeypatch.setattr("openprogram.state.paths.get_active_profile", lambda: None)
    assert windows._task_name() == windows.TASK_NAME

    monkeypatch.setattr("openprogram.state.paths.get_active_profile", lambda: "dev")
    name = windows._task_name()
    assert name != windows.TASK_NAME and name.startswith(windows.TASK_NAME)
    assert windows.install() == 0
    assert f"-TaskName '{name}'" in calls[-1]
    assert f"-TaskName '{windows.TASK_NAME}'" not in calls[-1]


def _launcher_for(monkeypatch, **environment) -> list[str]:
    monkeypatch.setattr(
        "openprogram.operations.worker.lifecycle._detached_worker_command",
        lambda: [r"C:\OpenProgram\python.exe", "-u", "-m", "openprogram", "worker", "run"],
    )
    for name in windows._LAUNCHER_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return windows._build_script().split("\r\n")


def test_a_named_profile_launcher_runs_that_profile(monkeypatch) -> None:
    # Task Scheduler starts the task with the logon environment, not the
    # environment of the shell that ran `--profile dev worker install`.
    lines = _launcher_for(
        monkeypatch,
        OPENPROGRAM_PROFILE="dev",
        OPENPROGRAM_STATE_DIR=r"D:\State & Data\100%\dev",
        OPENPROGRAM_WEB_PORT="18200",
    )

    worker = next(i for i, line in enumerate(lines) if "worker run" in line)
    assert 'set "OPENPROGRAM_PROFILE=dev"' in lines[:worker]
    assert 'set "OPENPROGRAM_WEB_PORT=18200"' in lines[:worker]
    assert r'set "OPENPROGRAM_STATE_DIR=D:\State & Data\100%%\dev"' in lines[:worker]


def test_the_default_profile_launcher_sets_no_profile(monkeypatch) -> None:
    lines = _launcher_for(monkeypatch)

    assert not [line for line in lines if line.startswith("set ")]
    assert "chcp 65001 >nul" not in lines


def test_the_launcher_carries_no_secret_from_the_installing_shell(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    lines = _launcher_for(monkeypatch, OPENPROGRAM_PROFILE="dev")

    assert "OPENAI_API_KEY" not in "\n".join(lines)
    assert not [line for line in lines if line.startswith('set "PATH=')]


def test_a_launcher_with_a_non_ascii_path_is_read_as_utf8(monkeypatch) -> None:
    # cmd reads a batch file in the console code page; the file is UTF-8.
    lines = _launcher_for(monkeypatch, OPENPROGRAM_STATE_DIR="C:\\Users\\\u5f20\u4e09\\state")

    assert lines[:2] == ["@echo off", "chcp 65001 >nul"]


def test_install_refuses_a_value_cmd_cannot_hold_and_leaves_the_worker_running(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    launcher, calls = _installed(tmp_path, monkeypatch, worker_pid=4242)
    _launcher_for(monkeypatch)
    monkeypatch.setenv("OPENPROGRAM_STATE_DIR", 'C:\\state" & calc & "')

    assert windows.install() == 1
    assert calls == []
    assert launcher.read_text(encoding="utf-8") == "stale launcher"
    assert "OPENPROGRAM_STATE_DIR" in capsys.readouterr().out
