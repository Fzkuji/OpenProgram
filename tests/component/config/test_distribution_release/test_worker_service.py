"""release worker service tests."""
from __future__ import annotations
from types import SimpleNamespace
from ._support import Path, plistlib, sys


def test_launchd_replacement_unloads_keepalive_before_stopping_worker(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.operations.worker.services import launchd
    from openprogram.operations.worker import lifecycle

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
    events: list[str] = []

    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(
        launchd,
        "_launchctl",
        lambda *args: (events.append(f"launchctl:{args[0]}") or (0, "")),
    )
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: 12345)
    monkeypatch.setattr(
        lifecycle,
        "stop_worker",
        lambda **kwargs: (events.append(f"stop:{kwargs}") or 0),
    )

    assert launchd.install() == 0
    assert events == [
        "launchctl:list",
        "launchctl:unload",
        "stop:{'prefer_service': False}",
        "launchctl:load",
    ]



def test_launchd_replaces_an_unloaded_stale_plist(tmp_path: Path, monkeypatch) -> None:
    from openprogram.operations.worker.services import launchd
    from openprogram.operations.worker import lifecycle

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
    events: list[str] = []

    def fake_launchctl(*args: str) -> tuple[int, str]:
        events.append(f"launchctl:{args[0]}")
        if args[0] == "list":
            return 113, f'Could not find service "{launchd.LABEL}"'
        return 0, ""

    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(launchd, "_launchctl", fake_launchctl)
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: None)

    assert launchd.install() == 0
    assert events == ["launchctl:list", "launchctl:load"]
    assert plist_path.is_file()



def test_worker_stop_unloads_the_launchd_job_instead_of_signalling_it(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.operations.worker import lifecycle, services
    from openprogram.operations.worker.services import launchd

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
    events: list[str] = []

    def fake_launchctl(*args: str) -> tuple[int, str]:
        events.append(f"launchctl:{args[0]}")
        return 0, "loaded"

    monkeypatch.setattr(services, "_backend", lambda: launchd)
    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(launchd, "_launchctl", fake_launchctl)
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: None)
    monkeypatch.setattr(
        lifecycle.os, "kill", lambda *args: events.append("kill"),
    )

    assert lifecycle.stop_worker() == 0
    assert events == ["launchctl:list", "launchctl:unload"]
    assert plist_path.is_file()



def test_launchd_stop_then_stops_a_worker_still_holding_the_lock(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.operations.worker import lifecycle
    from openprogram.operations.worker.services import launchd

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
    events: list[str] = []

    def fake_launchctl(*args: str) -> tuple[int, str]:
        events.append(f"launchctl:{args[0]}")
        if args[0] == "list":
            return 113, f'Could not find service "{launchd.LABEL}"'
        return 0, ""

    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(launchd, "_launchctl", fake_launchctl)
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: 12345)
    monkeypatch.setattr(
        lifecycle,
        "stop_worker",
        lambda **kwargs: (events.append(f"stop:{kwargs}") or 0),
    )

    assert launchd.stop_if_installed() == 0
    assert events == ["launchctl:list", "stop:{'prefer_service': False}"]



def test_launchd_start_and_restart_reload_through_install(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.operations.worker.services import launchd

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(launchd, "install", lambda: 7)

    assert launchd.start_if_installed() is None
    assert launchd.stop_if_installed() is None
    assert launchd.restart_if_installed() is None

    plist_path.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
    assert launchd.start_if_installed() == 7
    assert launchd.restart_if_installed() == 7



def test_launchd_unload_failure_preserves_the_service_plist(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.operations.worker.services import launchd

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    original = plistlib.dumps({"Label": launchd.LABEL})
    plist_path.write_bytes(original)

    def fake_launchctl(*args: str) -> tuple[int, str]:
        if args[0] == "list":
            return 0, "loaded"
        return 5, "synthetic unload failure"

    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(launchd, "_launchctl", fake_launchctl)

    assert launchd.uninstall() == 5
    assert plist_path.read_bytes() == original



def test_launchd_worker_preserves_packaged_python_flags(monkeypatch) -> None:
    from openprogram.operations.worker import lifecycle
    from openprogram.operations.worker.services import launchd

    # The import system reads other sys.flags fields while _build_plist
    # imports lazily, so keep every real flag and override only these two.
    flags = SimpleNamespace(**{
        name: getattr(sys.flags, name) for name in dir(sys.flags)
        if not name.startswith("_") and isinstance(getattr(sys.flags, name), (int, bool))
    })
    flags.isolated, flags.dont_write_bytecode = 1, 1
    monkeypatch.setattr(lifecycle.sys, "flags", flags)

    assert launchd._build_plist()["ProgramArguments"] == [
        sys.executable,
        "-I",
        "-B",
        "-u",
        "-m",
        "openprogram",
        "worker",
        "run",
    ]
    assert "ProcessType" not in launchd._build_plist()



def test_detached_worker_on_windows_keeps_the_utf8_mode_its_launcher_set() -> None:
    from openprogram.operations.worker.lifecycle import _detached_worker_command

    flags = SimpleNamespace(isolated=1, dont_write_bytecode=1, utf8_mode=1)
    assert _detached_worker_command(flags, windows=True)[1:] == [
        "-I", "-B", "-X", "utf8", "-u", "-m", "openprogram", "worker", "run",
    ]
    # Elsewhere the mode follows the locale, and a service file names no option for it.
    assert "-X" not in _detached_worker_command(flags, windows=False)
    flags.utf8_mode = 0
    assert "-X" not in _detached_worker_command(flags, windows=True)


def test_detached_worker_preserves_packaged_python_flags() -> None:
    from openprogram.operations.worker.lifecycle import _detached_worker_command

    command = _detached_worker_command(
        SimpleNamespace(isolated=1, dont_write_bytecode=1)
    )
    assert command[1:] == [
        "-I",
        "-B",
        "-u",
        "-m",
        "openprogram",
        "worker",
        "run",
    ]



def test_linux_worker_process_probe_treats_zombie_as_stopped(monkeypatch) -> None:
    from openprogram.operations.worker import lifecycle

    monkeypatch.setattr(lifecycle.sys, "platform", "linux")
    monkeypatch.setattr(
        lifecycle.Path,
        "read_text",
        lambda self, **kwargs: "123 (openprogram) Z 1 2 3",
    )
    assert lifecycle._process_alive(123) is False



def test_launchd_named_profile_gets_its_own_job(monkeypatch) -> None:
    from openprogram.operations.worker.services import launchd

    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)
    default_plist = launchd._plist_path()
    assert launchd._build_plist()["Label"] == launchd.LABEL
    assert "OPENPROGRAM_PROFILE" not in launchd._build_plist()["EnvironmentVariables"]

    monkeypatch.setenv("OPENPROGRAM_PROFILE", "demo")
    plist = launchd._build_plist()
    assert plist["Label"] != launchd.LABEL
    assert launchd._plist_path() != default_plist
    assert launchd._plist_path().name == f"{plist['Label']}.plist"
    assert plist["EnvironmentVariables"]["OPENPROGRAM_PROFILE"] == "demo"
