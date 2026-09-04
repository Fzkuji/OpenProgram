"""App-independent recovery stays bound to the original update."""
import os
import json
import plistlib
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from types import SimpleNamespace

import pytest

from tests.component.self_update.test_launcher import _request, _trusted_installer, _ready
from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.self_update.test_system_probe import live  # noqa: F401
from tests.component.self_update.test_verification_channel import verifier  # noqa: F401
from tests.component.self_update.test_install_transaction import installation, version  # noqa: F401
from tests.component.self_update.test_commit_recovery import accepted  # noqa: F401
from tests.component.self_update.test_owner_repair import recoverable, manual  # noqa: F401
from tests.component.config.test_distribution_release import MACOS_DESKTOP_INSTALL
from tests.component.self_update.test_controller_bundle import native_workspace  # noqa: F401

pytestmark = [pytest.mark.macos, MACOS_DESKTOP_INSTALL]


def test_public_launcher_publishes_independent_recovery_before_submit(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.self_update import launcher
    profile = tmp_path / "profile"
    store = _request(profile)
    _, digest = _trusted_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    directory = store.root / "su_launch"
    calls = []
    def launchctl(*args):
        calls.append(args)
        if args[0] == "submit":
            assert (directory / "recover.sh").is_file()
            assert (directory / "bootstrap.json").is_file()
            (directory / "supervisor.ready").write_text(_ready("su_launch", digest))
        return (113, "not found") if args[0] == "print" else (0, str(os.getpid()))
    monkeypatch.setattr(launcher, "_launchctl", launchctl)
    assert launcher.launch_supervisor("su_launch").submitted
    assert all(call[0] != "bootstrap" for call in calls)
    binding = json.loads((directory / "bootstrap.json").read_text())
    plist = Path(binding["plist_path"])
    payload = plistlib.loads(plist.read_bytes())
    assert payload["RunAtLoad"] is True
    assert not {"KeepAlive", "StartInterval", "StartCalendarInterval"} & payload.keys()
    assert payload["ProgramArguments"] == ["/bin/sh", str(directory / "recover.sh"), "resume"]
    assert plist.stat().st_mode & 0o777 == 0o600
    assert (directory / "recover.sh").stat().st_mode & 0o777 == 0o700


@pytest.fixture
def bootstrap_request(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.self_update import bootstrap, controller_bundle
    store = _request(tmp_path / "profile")
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    source, digest = _trusted_installer(tmp_path, monkeypatch)
    directory = store.root / "su_launch"
    bundle = controller_bundle.prepare_controller(directory)
    with store._locked():
        bootstrap.prepare_bootstrap(store, store._load_unlocked("su_launch"), bundle)
    return SimpleNamespace(store=store, directory=directory, bundle=bundle, source=source,
                           plist=bootstrap._agents_directory() / "ai.openprogram.self-update.recovery.su_launch.plist")


def invoke(v, mode):
    from openprogram.self_update import bootstrap
    return bootstrap.main(["--state-root", str(v.store.root), "--installer-sha256", v.bundle.installer_sha256,
                           "--mode", mode, v.directory.name])


def test_status_is_read_only_and_repreparation_keeps_bound_files(bootstrap_request, capsys):
    from openprogram.self_update import bootstrap
    v = bootstrap_request
    files = [v.directory / name for name in ("request.json", "state.json", "bootstrap.json", "recover.sh")]
    files.append(v.plist)
    before = {path: path.read_bytes() for path in files}
    assert invoke(v, "status") == 0
    assert json.loads(capsys.readouterr().out)["recovery_script"] == str(v.directory / "recover.sh")
    with v.store._locked():
        bootstrap.prepare_bootstrap(v.store, v.store._load_unlocked(v.directory.name), v.bundle)
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not (v.directory / "bootstrap-error.json").exists()


@pytest.mark.parametrize("damage", ["script", "binding", "plist", "symlink", "fifo", "permissions", "parent", "runtime"])
def test_changed_bootstrap_rejects_resume_before_any_supervisor(bootstrap_request, monkeypatch, damage):
    from openprogram.self_update import supervisor
    v = bootstrap_request
    if damage in {"script", "binding", "plist"}:
        target = {"script": v.directory / "recover.sh", "binding": v.directory / "bootstrap.json", "plist": v.plist}[damage]
        target.write_bytes(target.read_bytes() + b"changed")
    elif damage in {"symlink", "fifo"}:
        v.plist.rename(v.plist.with_suffix(".retained"))
        if damage == "fifo":
            os.mkfifo(v.plist, 0o600)
        else:
            v.plist.symlink_to(v.plist.with_suffix(".retained"))
    elif damage == "permissions":
        v.plist.chmod(0o666)
    elif damage == "parent":
        v.plist.parent.chmod(0o777)
    else:
        v.bundle.python.write_text("changed saved runtime")
    before = (v.directory / "state.json").read_bytes()
    monkeypatch.setattr(supervisor, "run_supervisor", lambda *a, **k: pytest.fail("invalid recovery invoked supervisor"))
    assert invoke(v, "resume") == 1
    assert (v.directory / "state.json").read_bytes() == before
    assert (v.directory / "bootstrap-error.json").is_file()
    assert v.plist.exists() or v.plist.is_symlink()


@pytest.mark.parametrize("changed", [False, True])
def test_completed_update_cleans_only_its_exact_login_file(bootstrap_request, changed):
    from openprogram.self_update import bootstrap, UpdatePhase
    v = bootstrap_request
    v.store.transition(v.directory.name, UpdatePhase.ABORTED)
    before = (v.directory / "state.json").read_bytes()
    other = v.plist.with_name("unrelated.plist")
    other.write_text("unrelated user task")
    if changed:
        v.plist.write_bytes(b"unrelated replacement")
    bootstrap.cleanup_bootstrap(v.store, v.directory.name)
    assert v.plist.exists() is changed
    if changed:
        assert v.plist.read_bytes() == b"unrelated replacement"
        assert (v.directory / "bootstrap-error.json").exists()
    else:
        assert invoke(v, "resume") == 0
    assert (v.directory / "state.json").read_bytes() == before
    assert v.bundle.python.exists() and (v.directory / "recover.sh").exists()
    assert other.read_text() == "unrelated user task"


def bind_native(v):
    from openprogram.self_update import bootstrap, controller_bundle
    bundle = controller_bundle._load_bundle(v.update / "controller")
    with v.v.store._locked():
        bootstrap.prepare_bootstrap(v.v.store, v.v.store._load_unlocked(v.v.request.update_id), bundle)
    return SimpleNamespace(store=v.v.store, directory=v.update, bundle=bundle,
                           plist=bootstrap._agents_directory() / f"ai.openprogram.self-update.recovery.{v.v.request.update_id}.plist")


def test_bootstrap_restores_missing_app_with_original_native_transaction(recoverable):
    from openprogram.self_update import UpdatePhase
    v = recoverable
    entry = bind_native(v)
    # Simulate controller loss with a missing canonical App and unusable verification.
    v.target.rename(v.tmp / "retained-candidate.app")
    (v.update / "verifier-grant-1.json").rename(v.update / "retained-grant.json")
    assert invoke(entry, "resume") == 1  # Original feature goal failed, service restored.
    state = v.v.store.load(v.v.request.update_id).state
    assert state.phase is UpdatePhase.ROLLED_BACK
    assert state.detail["restored_system_gate"]["candidate_sha"] == "3" * 40
    assert version(v.target) == "0.6.1"
    assert not (v.v.store.root / "maintenance.json").exists() and not entry.plist.exists()
    assert len(v.v.runner.list_jobs("p1")) == 1 and v.commands == ["--rollback"]
    assert invoke(entry, "resume") == 0 and v.commands == ["--rollback"]


def test_bootstrap_manual_state_still_requires_interactive_consent(recoverable, monkeypatch):
    import sys
    v = recoverable
    manual(v)
    entry = bind_native(v)
    before = (v.update / "state.json").read_bytes()
    assert invoke(entry, "resume") == 1
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert invoke(entry, "repair") == 1
    assert not (v.update / "owner-repair.json").exists() and v.commands == []
    assert (v.update / "state.json").read_bytes() == before and entry.plist.exists()


def test_saved_runtime_script_and_native_login_survive_original_app_removal(native_workspace, monkeypatch):
    from openprogram import paths
    from openprogram.self_update import bootstrap, controller_bundle, UpdatePhase
    from tests.component.self_update.test_install_transaction import INSTALLER
    tmp = native_workspace
    installed = Path("/Applications/OpenProgram.app/Contents/Resources/runtime")
    if not (installed / "runtime-manifest.json").is_file():
        pytest.skip("requires the installed macOS standalone runtime")
    resources = tmp / "fixture-resources"
    runtime = resources / "runtime"
    shutil.copytree(installed, runtime, symlinks=True)
    package = next((runtime / "python").glob("*/lib/python*/site-packages/openprogram"))
    shutil.copytree(Path(bootstrap.__file__).parent, package / "self_update", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    command = Path(__file__).resolve().parents[3] / "apps/cli/python/openprogram_cli/_impl/commands/self_update.py"
    shutil.copy2(command, package.parent / "openprogram_cli/_impl/commands/self_update.py")
    (resources / "update").mkdir()
    shutil.copy2(INSTALLER, resources / "update/install-app.sh")
    owner_home = tmp / "owner"
    owner_home.mkdir(mode=0o700)
    monkeypatch.setattr(paths, "get_state_dir", lambda: owner_home / ".openprogram")
    monkeypatch.setattr(controller_bundle, "_installed_resources", lambda: resources)
    monkeypatch.setattr(controller_bundle, "controller_environment", lambda: {
        "HOME": str(owner_home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    })
    monkeypatch.setattr(bootstrap, "_agents_directory", lambda: owner_home / "Library/LaunchAgents")
    update_id = "su_native_bootstrap_" + uuid.uuid4().hex[:12]
    store = _request(owner_home / ".openprogram", update_id)
    directory = store.root / update_id
    bundle = controller_bundle.prepare_controller(directory)
    with store._locked():
        bootstrap.prepare_bootstrap(store, store._load_unlocked(update_id), bundle)
    resources.rename(tmp / "retained-original-resources")
    script = directory / "recover.sh"
    env = {"HOME": str(tmp / "wrong-home"), "PATH": "/usr/bin:/bin", "PYTHONPATH": "/must-not-import"}
    def shell(*args):
        return subprocess.run(["/bin/sh", str(script), *args], capture_output=True, text=True, timeout=40, env=env)
    result = shell()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["update_id"] == update_id
    assert shell("arbitrary-command").returncode == 2 and shell("status", "extra").returncode == 2
    assert shell("repair").returncode == 1
    assert not (directory / "owner-repair.json").exists()
    store.transition(update_id, UpdatePhase.ABORTED)
    plist = bootstrap._agents_directory() / f"ai.openprogram.self-update.recovery.{update_id}.plist"
    domain = f"gui/{os.getuid()}/ai.openprogram.self-update.recovery.{update_id}"
    try:
        loaded = subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                                capture_output=True, text=True, timeout=15)
        assert loaded.returncode == 0, loaded.stderr
        deadline = time.monotonic() + 45
        while plist.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        log = directory / "bootstrap.log"
        assert not plist.exists(), log.read_text()[-2000:] if log.exists() else "no bootstrap log"
        assert script.exists() and bundle.python.exists()
        assert store.load(update_id).state.phase is UpdatePhase.ABORTED
        assert shell("resume").returncode == 0
    finally:
        subprocess.run(["/bin/launchctl", "bootout", domain], capture_output=True, text=True, timeout=15)
        assert subprocess.run(["/bin/launchctl", "print", domain], capture_output=True,
                              text=True, timeout=15).returncode != 0


def test_initial_conflict_does_not_overwrite_existing_login_file(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.self_update import bootstrap, launcher
    store = _request(tmp_path / "profile")
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    _trusted_installer(tmp_path, monkeypatch)
    directory = bootstrap._agents_directory()
    directory.mkdir(parents=True, mode=0o700)
    plist = directory / "ai.openprogram.self-update.recovery.su_launch.plist"
    plist.write_bytes(b"pre-existing unrelated content")
    plist.chmod(0o600)
    monkeypatch.setattr(launcher, "_launchctl", lambda *_: pytest.fail("conflicting preparation submitted"))
    with pytest.raises(launcher.LaunchError, match="differs"):
        launcher.launch_supervisor("su_launch")
    assert plist.read_bytes() == b"pre-existing unrelated content"
    assert not (store.root / "su_launch/supervisor.sh").exists()


def test_cleanup_diagnostic_failure_does_not_change_original_result(bootstrap_request, monkeypatch, capsys):
    from openprogram.self_update import bootstrap, UpdatePhase
    v = bootstrap_request
    v.store.transition(v.directory.name, UpdatePhase.ABORTED)
    original = (v.directory / "state.json").read_bytes()
    v.plist.write_bytes(b"changed login file")
    monkeypatch.setattr(v.store, "_write_json", lambda *a: (_ for _ in ()).throw(OSError("read-only filesystem")))
    bootstrap.cleanup_bootstrap(v.store, v.directory.name)
    assert "unable to persist diagnostic" in capsys.readouterr().err
    assert (v.directory / "state.json").read_bytes() == original
    assert v.plist.read_bytes() == b"changed login file"
