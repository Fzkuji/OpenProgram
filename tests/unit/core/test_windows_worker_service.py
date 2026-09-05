from __future__ import annotations

from pathlib import Path

from openprogram.worker.services import windows


def test_windows_service_launcher_preserves_runtime_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        "openprogram.worker.lifecycle._detached_worker_command",
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
        "openprogram.worker.lifecycle.current_worker_pid", lambda: None
    )
    monkeypatch.setattr(windows, "_powershell", lambda script: calls.append(script) or (0, ""))
    monkeypatch.setattr("openprogram._compat.restrict_to_user", lambda path: None)

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
