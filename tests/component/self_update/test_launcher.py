from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os

import pytest

from openprogram.self_update import SelfUpdateStore, UpdateRequest
from openprogram.self_update.launcher import LaunchError, launch_supervisor


def _request(profile: Path, update_id: str = "su_launch") -> SelfUpdateStore:
    store = SelfUpdateStore(profile / "self-updates")
    store.create(
        UpdateRequest(
            update_id=update_id,
            session_id="session-1",
            origin_turn_id="turn-1",
            origin_assistant_id="turn-1_reply",
            agent_id="main",
            repo="/tmp/OpenProgram",
            worktree_id="wt_candidate",
            base_sha="1" * 40,
            candidate_sha="2" * 40,
            changed_paths=("openprogram/feature.py",),
            pre_update_evidence=("git-status:clean",),
            goal="Add the requested behavior",
            assertions=("The behavior is observable",),
        )
    )
    return store


def _trusted_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    from openprogram.self_update import controller_bundle

    resources = tmp_path / "resources"
    (resources / "update").mkdir(parents=True)
    runtime = resources / "runtime"
    runtime.mkdir()
    python = runtime / "python"
    python.write_text("trusted interpreter")
    python.chmod(0o755)
    (runtime / "runtime-manifest.json").write_text(json.dumps({"schema": 2, "python": "python"}))
    installer = resources / "update/install-app.sh"
    installer.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(controller_bundle, "_installed_resources", lambda: resources)
    monkeypatch.setattr(controller_bundle, "_probe_runtime", lambda *_: None)
    return installer, hashlib.sha256(installer.read_bytes()).hexdigest()


def _ready(update_id: str, installer_sha256: str) -> str:
    return json.dumps(
        {
            "schema": 1,
            "pid": os.getpid(),
            "update_id": update_id,
            "installer_sha256": installer_sha256,
        }
    )


def test_launch_writes_private_fixed_controller_and_submits_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.self_update import launcher
    from openprogram import paths

    profile = tmp_path / "profile"
    root = profile / "self-updates"
    _request(profile)
    _source, installer_sha256 = _trusted_installer(tmp_path, monkeypatch)
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        if args[0] == "kickstart":
            return 0, str(os.getpid())
        if args[0] == "submit":
            (root / "su_launch" / "supervisor.ready").write_text(
                _ready("su_launch", installer_sha256)
            )
        return (113, "not found") if args[0] == "print" else (0, "")

    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)

    result = launch_supervisor("su_launch")
    script = root / "su_launch" / "supervisor.sh"
    installer = root / "su_launch" / "controller" / "install-app.sh"

    assert result.submitted is True
    assert result.label == "ai.openprogram.self-update.su_launch"
    assert script.stat().st_mode & 0o777 == 0o700
    assert installer.stat().st_mode & 0o777 == 0o700
    assert hashlib.sha256(installer.read_bytes()).hexdigest() == installer_sha256
    body = script.read_text(encoding="utf-8")
    assert str(root / "su_launch/controller/runtime/python") in body
    assert "/usr/bin/env -i" in body
    assert "openprogram.self_update.supervisor" in body
    assert "su_launch" in body
    assert installer_sha256 in body
    assert calls[0][:2] == ("print", f"gui/{launcher.os.getuid()}/{result.label}")
    assert calls[1][0:3] == ("submit", "-l", result.label)
    assert calls[1][-1] == str(script)


def test_duplicate_launch_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    from openprogram.self_update import launcher
    from openprogram import paths

    profile = tmp_path / "profile"
    _request(profile)
    _source, installer_sha256 = _trusted_installer(tmp_path, monkeypatch)
    (profile / "self-updates" / "su_launch" / "supervisor.ready").write_text(
        _ready("su_launch", installer_sha256)
    )
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, str(os.getpid()) if args[0] == "kickstart" else "already loaded"

    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)

    result = launch_supervisor("su_launch")

    assert result.submitted is False
    assert result.already_running is True
    assert len(calls) == 2
    assert calls[-1][0:2] == ("kickstart", "-p")


def test_launch_removes_job_when_controller_never_becomes_ready(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    _request(profile)
    _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(launcher, "_wait_ready", lambda *_args: False)
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        if args[0] == "kickstart":
            return 0, str(os.getpid())
        return (113, "not found") if args[0] == "print" else (0, "")

    monkeypatch.setattr(launcher, "_launchctl", launchctl)

    with pytest.raises(LaunchError, match="did not become ready"):
        launch_supervisor("su_launch")

    assert calls[-1] == ("remove", "ai.openprogram.self-update.su_launch")


def test_launch_failure_is_explicit_and_preserves_request(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.self_update import launcher
    from openprogram import paths

    profile = tmp_path / "profile"
    store = _request(profile)
    _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(
        launcher,
        "_launchctl",
        lambda *args: (113, "missing") if args[0] == "print" else (5, "denied"),
    )

    with pytest.raises(LaunchError, match="denied"):
        launch_supervisor("su_launch")

    assert store.load("su_launch").state.phase.value == "preparing"


def test_launch_does_not_submit_after_ambiguous_status_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    _request(profile)
    _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 5, "permission denied"

    monkeypatch.setattr(launcher, "_launchctl", launchctl)

    with pytest.raises(LaunchError, match="status failed"):
        launch_supervisor("su_launch")

    assert len(calls) == 1


def test_loaded_job_rejects_stale_ready_marker(tmp_path: Path, monkeypatch) -> None:
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    _request(profile)
    _source, installer_sha256 = _trusted_installer(tmp_path, monkeypatch)
    marker = profile / "self-updates" / "su_launch" / "supervisor.ready"
    marker.write_text(_ready("su_other", installer_sha256), encoding="utf-8")
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(launcher, "_launchctl", lambda *args: (0, str(os.getpid()) if args[0] == "kickstart" else "loaded"))

    with pytest.raises(LaunchError, match="did not become ready"):
        launch_supervisor("su_launch")


def test_launch_rejects_log_symlink(tmp_path: Path, monkeypatch) -> None:
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    root = profile / "self-updates"
    _request(profile)
    _source, installer_sha256 = _trusted_installer(tmp_path, monkeypatch)
    outside = tmp_path / "outside.log"
    (root / "su_launch" / "supervisor.log").symlink_to(outside)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)

    def launchctl(*args: str) -> tuple[int, str]:
        if args[0] == "submit":
            (root / "su_launch" / "supervisor.ready").write_text(
                _ready("su_launch", installer_sha256), encoding="utf-8"
            )
            return 0, ""
        return 113, "not found"

    monkeypatch.setattr(launcher, "_launchctl", launchctl)

    with pytest.raises(LaunchError, match="log path"):
        launch_supervisor("su_launch")
    assert outside.exists() is False


def test_concurrent_launch_submits_only_once(tmp_path: Path, monkeypatch) -> None:
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    _request(profile)
    _source, installer_sha256 = _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    calls: list[tuple[str, ...]] = []
    registered = False

    def launchctl(*args: str) -> tuple[int, str]:
        nonlocal registered
        calls.append(args)
        if args[0] == "kickstart":
            return 0, str(os.getpid())
        if args[0] == "submit":
            registered = True
            (profile / "self-updates" / "su_launch" / "supervisor.ready").write_text(
                _ready("su_launch", installer_sha256), encoding="utf-8"
            )
            return 0, ""
        return (0, "loaded") if registered else (113, "not found")

    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch_supervisor, ["su_launch", "su_launch"]))

    assert sum(call[0] == "submit" for call in calls) == 1
    assert sorted(result.submitted for result in results) == [False, True]


def test_launch_resubmits_after_controller_and_service_exit(tmp_path, monkeypatch) -> None:
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    _request(profile)
    _source, installer_sha256 = _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    marker = profile / "self-updates" / "su_launch" / "supervisor.ready"
    submissions = 0

    def launchctl(*args: str) -> tuple[int, str]:
        nonlocal submissions
        if args[0] == "kickstart":
            return 0, str(os.getpid())
        if args[0] == "submit":
            submissions += 1
            marker.write_text(_ready("su_launch", installer_sha256), encoding="utf-8")
            return 0, ""
        return 113, "not found"

    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    assert launch_supervisor("su_launch").submitted is True
    stale = json.loads(marker.read_text(encoding="utf-8"))
    stale["pid"] = 999999999
    marker.write_text(json.dumps(stale), encoding="utf-8")
    # The installed App can be replaced between controller process lifetimes.
    _source.write_text("replacement installer", encoding="utf-8")

    assert launch_supervisor("su_launch").submitted is True
    assert submissions == 2


def test_runtime_snapshot_failure_never_submits_launchd(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.self_update import launcher, controller_bundle

    profile = tmp_path / "profile"
    store = _request(profile)
    _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(launcher, "_launchctl", lambda *_: pytest.fail("must not contact launchd"))
    def fail(*_):
        raise ValueError("copied runtime failed")
    monkeypatch.setattr(controller_bundle, "_probe_runtime", fail)
    with pytest.raises(LaunchError, match="copied runtime failed"):
        launch_supervisor("su_launch")
    assert store.load("su_launch").state.phase.value == "preparing"
    assert not (store.root / "su_launch/controller").exists()


def test_loaded_stopped_controller_is_started_without_killing_it(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    _request(profile)
    _, digest = _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    marker = profile / "self-updates/su_launch/supervisor.ready"
    calls = []

    def launchctl(*args):
        calls.append(args)
        if args[0] == "kickstart":
            marker.write_text(_ready("su_launch", digest))
            return 0, str(os.getpid())
        return 0, "loaded but stopped"

    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    result = launch_supervisor("su_launch")
    assert result.submitted is False and result.already_running is False
    assert calls == [
        ("print", f"gui/{os.getuid()}/{result.label}"),
        ("kickstart", "-p", f"gui/{os.getuid()}/{result.label}"),
    ]


def test_ready_marker_for_unrelated_live_process_is_rejected(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.self_update import launcher
    profile = tmp_path / "profile"
    _request(profile)
    _, digest = _trusted_installer(tmp_path, monkeypatch)
    marker = profile / "self-updates/su_launch/supervisor.ready"
    marker.write_text(_ready("su_launch", digest))  # This test process is alive.
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(launcher, "_launchctl", lambda *args: (0, str(os.getpid() + 100000) if args[0] == "kickstart" else "loaded"))
    with pytest.raises(LaunchError, match="did not become ready"):
        launch_supervisor("su_launch")


def test_native_launchd_reuses_running_process_and_restarts_after_death(tmp_path, monkeypatch):
    import shlex
    import sys
    import time
    import uuid
    from openprogram import paths
    from openprogram.self_update import launcher

    if sys.platform != "darwin" or not Path("/bin/launchctl").is_file():
        pytest.skip("requires native macOS launchd")
    profile = tmp_path / "profile"
    update_id = "su_native_" + uuid.uuid4().hex
    store = _request(profile, update_id)
    installed, digest = _trusted_installer(tmp_path, monkeypatch)
    marker = store.root / update_id / "supervisor.ready"
    # A bounded inert controller interpreter fixture. The real launcher still
    # freezes it, generates the fixed script, and uses actual launchctl/PIDs.
    fixture = (
        "import os,json,time; from pathlib import Path; "
        f"Path({str(marker)!r}).write_text(json.dumps(dict(schema=1,pid=os.getpid(),"
        f"update_id={update_id!r},installer_sha256={digest!r}))); time.sleep(30)"
    )
    python = installed.parent.parent / "runtime/python"
    python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -c {shlex.quote(fixture)}\n")
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    label = f"ai.openprogram.self-update.{update_id}"
    domain = f"gui/{os.getuid()}/{label}"
    try:
        first = launch_supervisor(update_id)
        assert first.submitted
        first_pid = json.loads(marker.read_text())["pid"]
        assert first_pid != os.getpid()
        again = launch_supervisor(update_id, resume=True)
        assert not again.submitted and again.already_running
        assert json.loads(marker.read_text())["pid"] == first_pid
        rc, message = launcher._launchctl("kill", "SIGKILL", domain)
        assert rc == 0, message
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(first_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("native fixture did not exit")
        recovered = launch_supervisor(update_id, resume=True)
        assert not recovered.already_running
        assert json.loads(marker.read_text())["pid"] != first_pid
    finally:
        launcher._launchctl("remove", label)


@pytest.mark.parametrize("outcome", ["aborted", "succeeded", "rolled_back", "needs_manual_recovery"])
def test_terminal_maintenance_uses_only_saved_controller(tmp_path, monkeypatch, outcome):
    import time
    from openprogram import paths
    from openprogram.self_update import launcher, controller_bundle, UpdatePhase
    profile = tmp_path / "profile"
    store = _request(profile)
    _, digest = _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    marker = store.root / "su_launch/supervisor.ready"
    calls = []
    def launchctl(*args):
        calls.append(args)
        if args[0] == "kickstart":
            marker.write_text(_ready("su_launch", digest))
            return 0, str(os.getpid())
        return 0, "loaded"
    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    launch_supervisor("su_launch")
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY):
        store.transition("su_launch", phase)
    store._write_json(store.root / "maintenance.json", dict(schema=1, update_id="su_launch", entered_at=time.time()))
    if outcome != "aborted":
        for phase in (UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING):
            store.transition("su_launch", phase)
    store.transition("su_launch", UpdatePhase(outcome))
    monkeypatch.setattr(controller_bundle, "_installed_resources", lambda: pytest.fail("must not inspect replacement App"))
    calls.clear()
    result = launch_supervisor("su_launch", resume=True)
    assert len(calls) == (0 if outcome == "needs_manual_recovery" else 2)
    assert not result.submitted
    calls.clear()
    (store.root / "maintenance.json").unlink()
    launch_supervisor("su_launch", resume=True)
    assert calls == []
