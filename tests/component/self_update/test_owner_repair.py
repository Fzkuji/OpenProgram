"""Owner-confirmed recovery uses the saved controller without a chat turn."""
import json
import os
import sys
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from openprogram.self_update import UpdatePhase, supervisor
from openprogram.self_update.launcher import launch_supervisor as real_launch_supervisor
from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.self_update.test_system_probe import live  # noqa: F401
from tests.component.self_update.test_verification_channel import verifier  # noqa: F401
from tests.component.self_update.test_install_transaction import installation, phase  # noqa: F401
from tests.component.self_update.test_commit_recovery import accepted, run, interrupt  # noqa: F401
from tests.component.config.test_distribution_release import MACOS_DESKTOP_INSTALL

pytestmark = [pytest.mark.macos, MACOS_DESKTOP_INSTALL]


@pytest.fixture
def recoverable(accepted, monkeypatch):
    from openprogram.self_update import controller_bundle, launcher
    from openprogram.webui.routes import misc
    from tests.component.self_update.test_launcher import _trusted_installer
    from tests.component.self_update.test_install_transaction import INSTALLER
    v = accepted
    source, _ = _trusted_installer(v.tmp, monkeypatch)
    source.write_bytes(INSTALLER.read_bytes())
    (v.update / "controller").rename(v.update / "retained-controller-fixture")
    bundle = controller_bundle.prepare_controller(v.update)
    assert bundle.installer_sha256 == v.digest
    monkeypatch.setattr(controller_bundle, "_installed_resources", lambda: pytest.fail("replacement App must not supply controller"))
    def command(argument, directory, sha, mode, **kwargs):
        assert 0 < kwargs.get("timeout_seconds", 300) <= 300
        result = v.command(argument, directory, sha, mode)
        if mode.startswith("--restart-terminal:"):
            v.v.flags["pid"] = os.getpid() + 1000
            monkeypatch.setattr(misc, "os", SimpleNamespace(getpid=lambda: v.v.flags["pid"]))
        return result
    monkeypatch.setattr(supervisor, "_installer_command", command)
    monkeypatch.setattr(launcher, "launch_supervisor", lambda _id, **_: run(v))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: prompt.split("'")[1])
    return v


def cli(monkeypatch, *args):
    import openprogram.cli as application
    monkeypatch.setattr(sys, "argv", ["openprogram", "self-update", *args])
    with pytest.raises(SystemExit) as result:
        application.main()
    return result.value.code


def test_public_status_without_update_does_not_create_intent(tmp_path, monkeypatch, capsys):
    root = tmp_path / "profile"
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root)
    assert cli(monkeypatch, "status", "--json") == 0
    assert json.loads(capsys.readouterr().out)["update_id"] is None
    assert not (root / "self-updates").exists()


def test_public_noninteractive_repair_is_rejected_without_intent(accepted, monkeypatch):
    v = accepted
    v.v.store.transition(v.v.request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY,
                         detail=v.v.store.load(v.v.request.update_id).state.detail)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli(monkeypatch, "repair", v.v.request.update_id) == 1
    assert not list(v.update.glob("owner-repair*.json"))
    assert v.commands == [] and phase(v.transaction) == "activated"


@pytest.mark.parametrize("outcome", ["rollback", "commit"])
def test_public_owner_repair_restores_service_without_new_verifier(recoverable, monkeypatch, outcome):
    v = recoverable
    if outcome == "commit":
        owner_command = supervisor._installer_command
        interrupt(v, monkeypatch)  # Accepted decision + real native commit, before terminal state.
        monkeypatch.setattr(supervisor, "_installer_command", owner_command)
    before = v.v.store.load(v.v.request.update_id)
    v.v.store.transition(v.v.request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY, detail=before.state.detail)
    state = (v.update / "state.json").read_bytes()
    grant = (v.update / "verifier-grant-1.json").read_bytes()
    assert cli(monkeypatch, "repair", v.v.request.update_id) == 0
    from openprogram.self_update.owner_repair import status
    current = status(v.v.request.update_id)
    assert not current["maintenance"]
    assert current["phase"] == "needs_manual_recovery"
    assert current["repair_result"]["status"] == "recovered"
    assert current["repair_result"]["resolution"] == ("restored-old" if outcome == "rollback" else "accepted-candidate")
    assert current["repair_result"]["system_gate"]["worker_pid"] == os.getpid() + 1000
    assert (v.update / "state.json").read_bytes() == state
    assert (v.update / "verifier-grant-1.json").read_bytes() == grant
    assert len(v.v.runner.list_jobs("p1")) == 1
    assert phase(v.transaction) == ("rolled_back" if outcome == "rollback" else "committed")
    commands = list(v.commands)
    assert run(v) == 0 and v.commands == commands


def manual(v):
    v.v.store.transition(v.v.request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY,
                         detail=v.v.store.load(v.v.request.update_id).state.detail)


def advance_probe_waits(monkeypatch):
    """Keep permanent-failure probes real; advance only the repair retry clock."""
    from openprogram.self_update import owner_repair
    elapsed = [0.0]
    monkeypatch.setattr(owner_repair, "time", SimpleNamespace(
        time=time.time, monotonic=lambda: time.monotonic() + elapsed[0],
        sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + max(seconds, 61)),
    ))


@pytest.mark.parametrize("confirmation", ["no", "changed", "eof"])
def test_confirmation_refusal_or_stale_plan_does_not_authorize(recoverable, monkeypatch, confirmation):
    v = recoverable
    manual(v)
    def answer(prompt):
        if confirmation == "eof":
            raise EOFError
        if confirmation == "changed":
            path = v.transaction / "transaction.json"
            value = json.loads(path.read_text())
            value["worker"] = not value["worker"]
            v.v.store._write_json(path, value)
            return prompt.split("'")[1]
        return "no"
    monkeypatch.setattr("builtins.input", answer)
    assert cli(monkeypatch, "repair", v.v.request.update_id) == 1
    assert not (v.update / "owner-repair.json").exists()
    assert v.commands == []


@pytest.mark.parametrize("damage", ["expired", "journal", "app", "doctor", "missing_controller"])
def test_approved_repair_fails_closed_without_clearing_maintenance(recoverable, monkeypatch, damage):
    from openprogram.self_update import owner_repair as repair
    from openprogram.self_update.verification_channel import _digest
    from tests.component.self_update.test_commit_recovery import expire
    v = recoverable
    manual(v)
    request = repair.approve_repair(v.v.request.update_id, _digest(repair.preview_repair(v.v.request.update_id)))
    if damage == "expired":
        expire(monkeypatch)
    elif damage == "journal":
        value = json.loads((v.transaction / "transaction.json").read_text())
        value["active_sha256"] = "f" * 64
        v.v.store._write_json(v.transaction / "transaction.json", value)
    elif damage == "app":
        (v.transaction / "previous.app/drift").write_text("changed")
    elif damage == "doctor":
        v.v.flags["doctor"] = False
        advance_probe_waits(monkeypatch)
    else:
        (v.update / "controller/manifest.json").rename(v.update / "retained-manifest.json")
    assert run(v) == 1
    assert repair.status(v.v.request.update_id)["maintenance"]
    assert repair.read_result(v.v.store, request)["status"] == "failed"
    before = list(v.commands)
    assert run(v) == 1 and v.commands == before


def test_concurrent_confirmations_keep_one_pending_request(recoverable):
    from concurrent.futures import ThreadPoolExecutor
    from openprogram.self_update import owner_repair as repair
    from openprogram.self_update.verification_channel import _digest
    v = recoverable
    manual(v)
    digest = _digest(repair.preview_repair(v.v.request.update_id))
    def approve(_):
        try:
            return repair.approve_repair(v.v.request.update_id, digest)["repair_id"]
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(approve, range(2)))
    assert sum(value is not None for value in values) == 1
    assert v.commands == []


def test_interrupted_repair_reuses_request_and_original_deadline(recoverable, monkeypatch):
    from openprogram.self_update import owner_repair as repair
    from openprogram.self_update.verification_channel import _digest
    v = recoverable
    manual(v)
    request = repair.approve_repair(v.v.request.update_id, _digest(repair.preview_repair(v.v.request.update_id)))
    command = supervisor._installer_command
    def interrupted(a, d, s, m, **kwargs):
        value = command(a, d, s, m, **kwargs)
        if m == "--rollback":
            raise SystemExit("after native rollback")
        return value
    monkeypatch.setattr(supervisor, "_installer_command", interrupted)
    with pytest.raises(SystemExit, match="after native rollback"):
        run(v)
    assert phase(v.transaction) == "rolled_back"
    monkeypatch.setattr(supervisor, "_installer_command", command)
    assert run(v) == 0
    assert repair.load_repair(v.v.store, v.v.store.load(v.v.request.update_id)) == request


def test_interrupted_success_cleanup_does_not_repeat_app_or_worker_action(recoverable, monkeypatch):
    from openprogram.self_update import owner_repair as repair
    from openprogram.self_update.verification_channel import _digest
    v = recoverable
    manual(v)
    request = repair.approve_repair(v.v.request.update_id, _digest(repair.preview_repair(v.v.request.update_id)))
    leave = repair._leave_maintenance_unlocked
    monkeypatch.setattr(repair, "_leave_maintenance_unlocked", lambda *_: (_ for _ in ()).throw(SystemExit("before marker deletion")))
    with pytest.raises(SystemExit):
        run(v)
    original = repair.read_result(v.v.store, request)
    assert original["status"] == "recovered"
    v.commands.clear()
    monkeypatch.setattr(repair, "_leave_maintenance_unlocked", leave)
    assert run(v) == 0
    assert v.commands == ["--verify-terminal:rolled_back"]
    assert repair.read_result(v.v.store, request) == original


@pytest.mark.parametrize("outcome", ["recovered", "failed", "expired"])
def test_startup_waits_for_approved_repair_without_new_job(recoverable, monkeypatch, outcome):
    from openprogram.self_update import owner_repair as repair, recovery, launcher
    from openprogram.self_update.verification_channel import _digest
    v = recoverable
    manual(v)
    request = repair.approve_repair(v.v.request.update_id, _digest(repair.preview_repair(v.v.request.update_id)))
    launches, waits = [], []
    monkeypatch.setattr(launcher, "launch_supervisor", lambda uid, **kw: launches.append((uid, kw)))
    clock = [time.monotonic()]
    def pause(_):
        waits.append(True)
        if outcome == "expired":
            clock[0] += repair.REPAIR_SECONDS + 1
        else:
            if outcome == "failed":
                v.v.flags["doctor"] = False
                advance_probe_waits(monkeypatch)
            assert run(v) == (0 if outcome == "recovered" else 1)
    monkeypatch.setattr(recovery, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock[0], sleep=pause))
    assert recovery.recover_pending_updates() is (outcome == "recovered")
    assert launches == [(v.v.request.update_id, {"resume": True})] and waits == [True]
    assert len(v.v.runner.list_jobs("p1")) == 1
    assert repair.load_repair(v.v.store, v.v.store.load(v.v.request.update_id)) == request


def test_owner_repair_launch_uses_distinct_label_and_saved_controller(recoverable, monkeypatch):
    from openprogram.self_update import owner_repair as repair, launcher
    from openprogram.self_update.controller_bundle import _load_bundle
    from openprogram.self_update.verification_channel import _digest
    from tests.component.self_update.test_launcher import _ready
    v = recoverable
    manual(v)
    request = repair.approve_repair(v.v.request.update_id, _digest(repair.preview_repair(v.v.request.update_id)))
    bundle = _load_bundle(v.update / "controller")
    script = v.update / "supervisor.sh"
    script.write_text(launcher._controller_body(v.v.request.update_id, v.v.store.root, v.digest, bundle.python))
    script.chmod(0o700)
    calls = []
    def launchctl(*args):
        calls.append(args)
        if args[0] == "print":
            return 113, "not found"
        if args[0] == "kickstart":
            (v.update / "supervisor.ready").write_text(_ready(v.v.request.update_id, v.digest))
            return 0, str(os.getpid())
        return 0, ""
    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    result = real_launch_supervisor(v.v.request.update_id, resume=True)
    assert result.label == f"ai.openprogram.self-update.{v.v.request.update_id}.repair.{request['repair_id']}"
    assert calls[1][0:3] == ("submit", "-l", result.label)
    assert calls[1][-1] == str(script) and calls[-1][0:2] == ("kickstart", "-p")
    assert len(v.v.runner.list_jobs("p1")) == 1 and v.commands == []


def test_expired_success_cleanup_preserves_receipt_and_needs_new_confirmation(recoverable, monkeypatch):
    from openprogram.self_update import owner_repair as repair
    from openprogram.self_update.verification_channel import _digest
    from tests.component.self_update.test_commit_recovery import expire
    v = recoverable
    manual(v)
    request = repair.approve_repair(v.v.request.update_id, _digest(repair.preview_repair(v.v.request.update_id)))
    leave = repair._leave_maintenance_unlocked
    monkeypatch.setattr(repair, "_leave_maintenance_unlocked", lambda *_: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        run(v)
    original = repair.read_result(v.v.store, request)
    monkeypatch.setattr(repair, "_leave_maintenance_unlocked", leave)
    expire(monkeypatch)
    v.commands.clear()
    assert run(v) == 1 and v.commands == []
    assert repair.status(v.v.request.update_id)["maintenance"]
    assert repair.cleanup_error(v.v.store, request) is not None
    assert repair.read_result(v.v.store, request) == original
    assert cli(monkeypatch, "repair", v.v.request.update_id) == 0
    assert repair.load_repair(v.v.store, v.v.store.load(v.v.request.update_id))["repair_id"] != request["repair_id"]


def test_cleanup_io_error_preserves_success_receipt(recoverable, monkeypatch):
    from openprogram.self_update import owner_repair as repair
    from openprogram.self_update.verification_channel import _digest
    v = recoverable
    manual(v)
    request = repair.approve_repair(v.v.request.update_id, _digest(repair.preview_repair(v.v.request.update_id)))
    monkeypatch.setattr(repair, "_leave_maintenance_unlocked", lambda *_: (_ for _ in ()).throw(OSError("marker removal failed")))
    assert run(v) == 1
    assert repair.read_result(v.v.store, request)["status"] == "recovered"
    assert repair.cleanup_error(v.v.store, request)["error"] == "marker removal failed"
    assert repair.status(v.v.request.update_id)["maintenance"]


def test_public_aborted_repair_checks_old_app_without_activation(live, installation, monkeypatch):
    from dataclasses import replace
    from openprogram.self_update import SelfUpdateStore, controller_bundle, launcher, owner_repair
    from openprogram.self_update.maintenance import enter_maintenance
    from openprogram.webui.routes import misc
    from tests.component.self_update.test_launcher import _trusted_installer
    from tests.component.self_update.test_install_transaction import prepare, INSTALLER
    record, _, _ = live
    store = SelfUpdateStore()
    native, artifact, target, tmp = installation
    transaction = prepare(native, artifact)
    store.transition(record.request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY)
    request = replace(record.request, update_id="su_aborted_owner")
    store.create(request)
    store.transition(request.update_id, UpdatePhase.STAGING)
    store.transition(request.update_id, UpdatePhase.READY, detail={"transaction_dir": str(transaction)})
    enter_maintenance(request.update_id)
    supervisor._abort(store, request.update_id, UpdatePhase.READY, "quiescence timed out")
    directory = store.root / request.update_id
    source, _ = _trusted_installer(tmp, monkeypatch)
    source.write_bytes(INSTALLER.read_bytes())
    bundle = controller_bundle.prepare_controller(directory)
    monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda p: transaction if p == transaction else pytest.fail("wrong transaction"))
    native_run = supervisor.subprocess.run
    modes = []
    def fixture_run(args, **kwargs):
        if args[:2] == ["/bin/bash", str(directory / "controller/install-app.sh")]:
            modes.append(args[2])
            kwargs["env"] = {"DESTDIR": str(target.parent.parent), "HOME": str(tmp / "home"),
                             "TMPDIR": str(tmp / "tmp"), "PATH": os.environ["PATH"]}
        return native_run(args, **kwargs)
    monkeypatch.setattr(supervisor.subprocess, "run", fixture_run)
    monkeypatch.setattr(misc, "_HEAD_SHA", "3" * 40)
    monkeypatch.setattr(launcher, "launch_supervisor", lambda uid, **kw: supervisor.run_supervisor(uid, state_root=store.root, installer_sha256=bundle.installer_sha256))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: prompt.split("'")[1])
    journal = (transaction / "transaction.json").read_bytes()
    assert cli(monkeypatch, "repair", request.update_id) == 0
    assert modes == ["--restart-terminal:prepared", "--verify-terminal:prepared"]
    assert (transaction / "transaction.json").read_bytes() == journal
    assert owner_repair.status(request.update_id)["repair_result"]["resolution"] == "unchanged-old"
    assert store.load(request.update_id).state.dispatch is None


@pytest.mark.parametrize("controller_running", [False, True])
def test_deterministic_launch_failure_requires_new_consent_unless_controller_is_running(recoverable, monkeypatch, controller_running):
    from contextlib import nullcontext
    from openprogram.self_update import owner_repair as repair, launcher
    from openprogram.self_update.verification_channel import _digest
    v = recoverable
    manual(v)
    request = repair.approve_repair(v.v.request.update_id, _digest(repair.preview_repair(v.v.request.update_id)))
    installer = v.update / "controller/install-app.sh"
    original = installer.read_bytes()
    installer.write_bytes(original + b"\n# damaged saved installer\n")
    monkeypatch.setattr(launcher, "launch_supervisor", real_launch_supervisor)
    monkeypatch.setattr(launcher, "_launchctl", lambda *_: pytest.fail("must fail before launchctl"))
    with supervisor._controller_lock(v.update) if controller_running else nullcontext():
        assert cli(monkeypatch, "repair", v.v.request.update_id) == 1
    assert repair.status(v.v.request.update_id)["maintenance"] and v.commands == []
    result = repair.read_result(v.v.store, request)
    if controller_running:
        assert result is None  # A running controller retains its original authority.
        return
    assert result is not None and result["status"] == "failed"
    installer.write_bytes(original)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "refused")
    monkeypatch.setattr(launcher, "launch_supervisor", lambda uid, **kw: run(v))
    assert cli(monkeypatch, "repair", v.v.request.update_id) == 1
    assert prompts and v.commands == [] and repair.status(v.v.request.update_id)["maintenance"]
    assert repair.load_repair(v.v.store, v.v.store.load(v.v.request.update_id)) == request


def test_public_repair_waits_for_restarted_worker_web_readiness(recoverable, monkeypatch):
    from openprogram.self_update import owner_repair, system_probe
    from openprogram.worker import lifecycle
    from openprogram.webui.routes import misc
    v = recoverable
    manual(v)
    lock_path, ready_path = v.tmp / "inert-child.lock", v.tmp / "inert-child.web-ready"
    child_code = (
        "from pathlib import Path; import sys,time; "
        "from openprogram.worker.lock import WorkerLock; "
        "lock=WorkerLock(); lock.path=Path(sys.argv[1]); assert lock.try_acquire(); "
        "time.sleep(2); Path(sys.argv[2]).touch(); time.sleep(30)"
    )
    # Real restart lifecycle, but only an inert fixture child, never a worker/App.
    monkeypatch.setattr(lifecycle, "_detached_worker_command", lambda: [
        sys.executable, "-c", child_code, str(lock_path), str(ready_path)])
    monkeypatch.setattr(lifecycle.paths, "log_path", lambda: v.tmp / "inert-child.log")
    monkeypatch.setattr(lifecycle, "read_worker_port", lambda: None)
    children, early_returns = [], []
    native_popen = subprocess.Popen
    def popen(*args, **kwargs):
        proc = native_popen(*args, **kwargs)
        if args[0][0:2] == [sys.executable, "-c"] and child_code in args[0]:
            children.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", popen)
    original_command = supervisor._installer_command
    watcher = None
    def command(argument, directory, sha, mode, **kwargs):
        nonlocal watcher
        result = original_command(argument, directory, sha, mode, **kwargs)
        if mode.startswith("--restart-terminal:"):
            v.v.flags["web"] = False
            def fixture_pid():
                return int(lock_path.read_text()) if lock_path.exists() else None
            monkeypatch.setattr(lifecycle, "current_worker_pid", fixture_pid)
            assert lifecycle.restart_worker() == 0
            early_returns.append(not ready_path.exists())
            v.v.flags["pid"] = fixture_pid()
            monkeypatch.setattr(misc, "os", SimpleNamespace(getpid=lambda: v.v.flags["pid"]))
            def become_ready():
                until = time.monotonic() + 8
                while not ready_path.exists() and time.monotonic() < until:
                    time.sleep(0.02)
                v.v.flags["web"] = ready_path.exists()
            watcher = threading.Thread(target=become_ready)
            watcher.start()
        return result
    monkeypatch.setattr(supervisor, "_installer_command", command)
    try:
        code = cli(monkeypatch, "repair", v.v.request.update_id)
        if watcher:
            watcher.join(timeout=10)
        assert early_returns == [True] and ready_path.exists()
        gate = system_probe._probe_system(v.v.store.load(v.v.request.update_id), "3" * 40, 10)
        assert gate["worker_pid"] == children[0].pid
        assert code == 0, owner_repair.status(v.v.request.update_id)
        assert len(v.v.runner.list_jobs("p1")) == 1
    finally:
        for proc in children:
            proc.terminate()
            proc.wait(timeout=5)
        if watcher:
            watcher.join(timeout=10)
