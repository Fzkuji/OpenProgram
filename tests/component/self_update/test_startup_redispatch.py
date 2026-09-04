"""Startup uses the original trusted launch path, not a new candidate snapshot."""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from openprogram.self_update import UpdatePhase
from openprogram.self_update.launcher import launch_supervisor
from openprogram.self_update.recovery import recover_pending_updates
from tests.component.self_update.test_launcher import _request, _trusted_installer, _ready
from tests.component.self_update.test_recovery import environment  # noqa: F401
from tests.component.agent.async_job_support import store_fixture  # noqa: F401


@pytest.mark.parametrize("phase", [UpdatePhase.PREPARING, UpdatePhase.STAGING, UpdatePhase.READY])
def test_startup_redispatches_original_controller_after_installed_app_changes(tmp_path, monkeypatch, phase):
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    store = _request(profile)
    installed, digest = _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    marker = store.root / "su_launch/supervisor.ready"
    calls = []

    def launchctl(*args):
        calls.append(args)
        if args[0] in {"submit", "kickstart"}:
            marker.write_text(_ready("su_launch", digest))
        return (113, "not found") if args[0] == "print" else (0, str(os.getpid()))

    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    launch_supervisor("su_launch")
    if phase is not UpdatePhase.PREPARING:
        store.transition("su_launch", UpdatePhase.STAGING)
    if phase is UpdatePhase.READY:
        store.transition("su_launch", UpdatePhase.READY)
    installed.write_text("replacement installer")
    calls.clear()
    assert recover_pending_updates() is True
    assert any(call[0] == "submit" for call in calls)
    assert (marker.parent / "controller/install-app.sh").read_text() == "#!/bin/sh\nexit 0\n"
    assert store.load("su_launch").state.phase is phase  # Never invent turn release.


@pytest.mark.parametrize("damage", ["missing_bundle", "changed_runtime", "missing_script", "script_symlink"])
def test_startup_never_snapshots_replacement_app_or_dispatches_on_damage(tmp_path, monkeypatch, damage):
    from openprogram import paths
    from openprogram.self_update import launcher

    profile = tmp_path / "profile"
    store = _request(profile)
    installed, digest = _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    update = store.root / "su_launch"
    def launchctl(*args):
        if args[0] == "submit":
            (update / "supervisor.ready").write_text(_ready("su_launch", digest))
        return (113, "not found") if args[0] == "print" else (0, str(os.getpid()))
    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    launch_supervisor("su_launch")
    if damage == "missing_bundle":
        (update / "controller").rename(update / "retained-controller")
    elif damage == "changed_runtime":
        (update / "controller/runtime/python").write_text("changed runtime")
    else:
        (update / "supervisor.sh").rename(update / "retained-supervisor.sh")
        if damage == "script_symlink":
            (update / "supervisor.sh").symlink_to(update / "retained-supervisor.sh")
    installed.write_text("replacement installer must never be copied")
    monkeypatch.setattr(launcher, "_launchctl", lambda *_: pytest.fail("damaged controller launched"))
    monkeypatch.setattr("openprogram.self_update.controller_bundle._installed_resources",
                        lambda: pytest.fail("recovery inspected the replacement App"))
    assert recover_pending_updates() is False
    assert store.load("su_launch").state.dispatch is None
    assert (update / "startup-error-1.json").is_file()
    if damage == "missing_bundle":
        assert not (update / "controller").exists()


def test_concurrent_startup_reuses_controller_and_creates_one_real_job(environment, tmp_path, monkeypatch):
    from openprogram.self_update import launcher
    store, runner, calls, request, release = environment
    installed, digest = _trusted_installer(tmp_path, monkeypatch)
    update = store.root / request.update_id
    launch_calls = []
    registered = False
    def launchctl(*args):
        nonlocal registered
        launch_calls.append(args)
        if args[0] == "print":
            return (0, "loaded") if registered else (113, "not found")
        if args[0] == "submit":
            registered = True
            (update / "supervisor.ready").write_text(_ready(request.update_id, digest))
        return 0, str(os.getpid())
    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    monkeypatch.setattr(launcher, "launch_supervisor", launch_supervisor)
    launch_supervisor(request.update_id)
    installed.write_text("replacement installer")
    release()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: recover_pending_updates(), range(2))) == [True, True]
    runner.await_job(f"self-update:{request.update_id}:verify:1", timeout=5)
    assert len(calls) == 1 and len(runner.list_jobs("p1")) == 1
    assert sum(call[0] == "submit" for call in launch_calls) == 1
    assert all("-k" not in call for call in launch_calls)


def test_terminal_update_is_not_relaunched(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.self_update import launcher
    profile = tmp_path / "profile"
    store = _request(profile)
    store.transition("su_launch", UpdatePhase.ABORTED)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(launcher, "_launchctl", lambda *_: pytest.fail("terminal update relaunched"))
    assert recover_pending_updates() is True
    result = launch_supervisor("su_launch", resume=True)
    assert not result.submitted and not result.already_running


@pytest.mark.parametrize("phase", [UpdatePhase.PREPARING, UpdatePhase.STAGING, UpdatePhase.READY])
def test_successful_redispatch_cannot_clear_durable_startup_failure(tmp_path, monkeypatch, phase):
    from openprogram import paths
    from openprogram.self_update import launcher
    profile = tmp_path / "profile"
    store = _request(profile)
    _, digest = _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    update = store.root / "su_launch"
    fail = False
    def launchctl(*args):
        if args[0] == "print":
            return 113, "not found"
        if args[0] == "kickstart" and fail:
            return 5, "temporary launch failure"
        (update / "supervisor.ready").write_text(_ready("su_launch", digest))
        return 0, str(os.getpid())
    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    launch_supervisor("su_launch")
    if phase is not UpdatePhase.PREPARING:
        store.transition("su_launch", UpdatePhase.STAGING)
    if phase is UpdatePhase.READY:
        store.transition("su_launch", phase)
    fail = True
    assert recover_pending_updates() is False
    error = update / "startup-error-1.json"
    original = error.read_bytes()
    fail = False
    assert recover_pending_updates() is False
    assert error.read_bytes() == original
    assert store.load("su_launch").state.dispatch is None
