"""Handing the controller to each platform's service manager."""
from __future__ import annotations

from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdateRequest
from openprogram.operations.self_update.delivery import controller, launcher, launcher_linux, launcher_windows
from openprogram.operations.worker import subsystems


def _store(tmp_path: Path, **overrides) -> SelfUpdateStore:
    store = SelfUpdateStore(tmp_path / "self-updates")
    store.create(UpdateRequest(
        update_id="su_launch", session_id="s", origin_turn_id="t", origin_assistant_id="t_reply", agent_id="main",
        repo="/tmp/r", worktree_id="wt", base_sha="1" * 40, candidate_sha="2" * 40, changed_paths=("a.py",),
        pre_update_evidence=("git-status:clean",), goal="g", assertions=("a",), task="t", kind="change", **overrides,
    ))
    return store


def test_the_controller_runs_from_the_old_versions_venv(tmp_path: Path) -> None:
    argv = controller.command(_store(tmp_path, controller_version="old-one"), "su_launch")

    assert Path(argv[0]).parts[-5:-2] == ("versions", "old-one", "venv") or "old-one" in argv[0]
    assert argv[1:] == ["-I", "-B", "-m", controller.MODULE, "su_launch"]


def test_an_update_without_a_controller_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(controller.ControllerLaunchError):
        controller.command(_store(tmp_path), "su_launch")


def test_the_environment_carries_no_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-passed")
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")

    env = controller.environment()

    assert "ANTHROPIC_API_KEY" not in env and env["OPENPROGRAM_PROFILE"] == "dev"


def test_launchd_restarts_only_on_abnormal_exit(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple] = []

    def launchctl(*args):
        calls.append(args)
        return (1, "") if args[0] == "print" else (0, "")

    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    launcher.submit_controller("ai.test", ["/py", "-m", "x"], {"HOME": "/h"}, tmp_path / "supervisor.log")

    definition = plistlib.loads((tmp_path / "controller.plist").read_bytes())
    assert definition["KeepAlive"] == {"SuccessfulExit": False} and definition["RunAtLoad"] is True
    assert definition["ProgramArguments"] == ["/py", "-m", "x"]
    assert calls[-1][0] == "bootstrap"


def test_a_relaunch_kicks_the_existing_launchd_job(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple] = []

    def launchctl(*args):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    launcher.submit_controller("ai.test", ["/py"], {}, tmp_path / "supervisor.log")

    assert calls[-1][0] == "kickstart"


def test_linux_uses_a_transient_user_service(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(launcher_linux.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(launcher_linux.subprocess, "run",
                        lambda argv, **_: seen.append(argv) or SimpleNamespace(returncode=0, stderr=""))

    launcher_linux.submit_controller("ai.test", ["/py", "-m", "x"], {"HOME": "/h"}, tmp_path / "supervisor.log")

    assert seen[0][:2] == ["/usr/bin/systemd-run", "--user"]
    assert "--property=Restart=on-failure" in seen[0] and "--setenv=HOME=/h" in seen[0]
    assert seen[0][-3:] == ["/py", "-m", "x"]


def test_linux_without_systemd_starts_it_detached(tmp_path: Path, monkeypatch) -> None:
    spawned: list[list[str]] = []
    monkeypatch.setattr(launcher_linux.shutil, "which", lambda name: None)
    monkeypatch.setattr(controller, "spawn_detached", lambda argv, env, log: spawned.append(argv))

    launcher_linux.submit_controller("ai.test", ["/py"], {}, tmp_path / "supervisor.log")

    assert spawned == [["/py"]]


def test_windows_creates_a_task_that_restarts_on_failure(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(launcher_windows.subprocess, "run",
                        lambda argv, **_: seen.append(argv) or SimpleNamespace(returncode=0, stdout="", stderr=""))

    launcher_windows.submit_controller("ai.test", ["C:\\py.exe", "-m", "x"], {"HOME": "C:\\h"}, tmp_path / "supervisor.log")

    xml = (tmp_path / "controller-task.xml").read_text(encoding="utf-16")
    assert "<RestartOnFailure>" in xml and "C:\\py.exe" in xml
    assert [argv[1] for argv in seen] == ["/Create", "/Run"]


def test_subsystems_report_what_started() -> None:
    subsystems.clear()
    subsystems.record("scheduler", subsystems.RUNNING)
    subsystems.record("channel:wechat", subsystems.FAILED, "no token")

    assert subsystems.snapshot() == {
        "scheduler": {"status": "running"}, "channel:wechat": {"status": "failed", "error": "no token"},
    }
    subsystems.clear()


def test_a_subsystem_that_stopped_is_a_regression() -> None:
    from openprogram.operations.self_update.control.switch import Failed, LiveHost

    reference = {"subsystems": {"scheduler": {"status": "running"}, "channel:x": {"status": "failed"}}}
    LiveHost.compare_subsystems(reference, {"scheduler": {"status": "running"}})
    with pytest.raises(Failed):
        LiveHost.compare_subsystems(reference, {"scheduler": {"status": "failed"}})
