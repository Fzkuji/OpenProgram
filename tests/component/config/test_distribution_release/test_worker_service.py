"""release worker service tests."""
from __future__ import annotations
from ._support import (
    Path,
    SimpleNamespace,
    plistlib,
    sys,
)


def test_launchd_replacement_unloads_keepalive_before_stopping_worker(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.worker.services import launchd
    from openprogram.worker import lifecycle

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
        lifecycle, "stop_worker", lambda: (events.append("stop") or 0)
    )

    assert launchd.install() == 0
    assert events == ["launchctl:list", "launchctl:unload", "stop", "launchctl:load"]



def test_launchd_replaces_an_unloaded_stale_plist(tmp_path: Path, monkeypatch) -> None:
    from openprogram.worker.services import launchd
    from openprogram.worker import lifecycle

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



def test_launchd_unload_failure_preserves_the_service_plist(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.worker.services import launchd

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
    from openprogram.worker import lifecycle
    from openprogram.worker.services import launchd

    flags = SimpleNamespace(isolated=1, dont_write_bytecode=1)
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



def test_detached_worker_preserves_packaged_python_flags() -> None:
    from openprogram.worker.lifecycle import _detached_worker_command

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
    from openprogram.worker import lifecycle

    monkeypatch.setattr(lifecycle.sys, "platform", "linux")
    monkeypatch.setattr(
        lifecycle.Path,
        "read_text",
        lambda self, **kwargs: "123 (openprogram) Z 1 2 3",
    )
    assert lifecycle._process_alive(123) is False

