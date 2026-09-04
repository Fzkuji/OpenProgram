from __future__ import annotations

import hashlib
from contextlib import nullcontext
import os
from pathlib import Path
import json
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from openprogram.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.self_update.supervisor import Artifact, run_supervisor


def _staging(root: Path, *, timeout_seconds: int = 1800) -> SelfUpdateStore:
    store = SelfUpdateStore(root)
    store.create(
        UpdateRequest(
            update_id="su_supervisor",
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
            goal="Add behavior",
            assertions=("Behavior works",),
            timeout_seconds=timeout_seconds,
        )
    )
    store.transition("su_supervisor", UpdatePhase.STAGING)
    return store


def _installer(root: Path) -> str:
    path = root / "su_supervisor" / "controller" / "install-app.sh"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_failure_aborts_before_maintenance_or_activation(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram import paths
    from openprogram.self_update import supervisor

    profile = tmp_path / "profile"
    root = profile / "self-updates"
    store = _staging(root)
    installer_sha256 = _installer(root)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    activated: list[Artifact] = []
    monkeypatch.setattr(
        supervisor,
        "_build_candidate",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("build failed")),
    )
    monkeypatch.setattr(
        supervisor,
        "_activate",
        lambda artifact, *_args: activated.append(artifact) or "tx",
    )

    assert run_supervisor(
        "su_supervisor", state_root=root, installer_sha256=installer_sha256
    ) == 1
    record = store.load("su_supervisor")
    assert record.state.phase is UpdatePhase.ABORTED
    assert "build failed" in record.state.detail["error"]
    assert activated == []
    assert (root / "maintenance.json").exists() is False


def test_success_persists_transaction_before_verifying(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram import paths
    from openprogram.self_update import supervisor

    profile = tmp_path / "profile"
    root = profile / "self-updates"
    store = _staging(root)
    installer_sha256 = _installer(root)
    app = root / "su_supervisor" / "artifact" / "OpenProgram.app"
    app.mkdir(parents=True)
    artifact = Artifact(path=app, sha256="a" * 64)
    transaction = root / "su_supervisor" / "transaction"
    transaction.mkdir()
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(supervisor, "_build_candidate", lambda *_args: artifact)
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda *_args: True)
    monkeypatch.setattr(supervisor, "_prepare_install", lambda *_args: str(transaction))
    # Packaging/reopen admission has real native coverage in test_reopen_producer.
    monkeypatch.setattr(supervisor, "_prepare_reopen_activation", lambda *_: None)
    def activate(*_args):
        current = store.load("su_supervisor")
        assert current.state.phase is UpdatePhase.ACTIVATING
        assert current.state.detail["transaction_dir"] == str(transaction)
        return str(transaction)
    monkeypatch.setattr(supervisor, "_activate", activate)
    monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda path: path)
    monkeypatch.setattr("openprogram.self_update.system_probe.probe_current_system",
                        lambda *_: {"candidate_sha": "3" * 40})
    gate = {"worker_pid": os.getpid(), "receipt": "real-probe-boundary"}
    observations = []
    def probe(record):
        assert record.state.phase is UpdatePhase.ACTIVATING
        assert record.state.detail["transaction_dir"] == str(transaction)
        observations.append(record.request.candidate_sha)
        return gate
    monkeypatch.setattr("openprogram.self_update.system_probe.probe_system", probe)
    monkeypatch.setattr(supervisor, "_finish_verification", lambda *_: 0)  # Isolate gate publication.

    assert run_supervisor(
        "su_supervisor", state_root=root, installer_sha256=installer_sha256
    ) == 0
    record = store.load("su_supervisor")
    assert record.state.phase is UpdatePhase.VERIFYING
    assert record.state.detail["transaction_dir"] == str(transaction)
    assert record.state.detail["artifact_sha256"] == artifact.sha256
    assert observations == [record.request.candidate_sha]
    assert record.state.detail["system_gate"] == gate
    assert (root / "maintenance.json").exists() is True


def test_quiescence_timeout_aborts_without_activation(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram import paths
    from openprogram.self_update import supervisor

    profile = tmp_path / "profile"
    root = profile / "self-updates"
    store = _staging(root)
    installer_sha256 = _installer(root)
    app = root / "su_supervisor" / "artifact" / "OpenProgram.app"
    app.mkdir(parents=True)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(
        supervisor, "_build_candidate", lambda *_args: Artifact(app, "a" * 64)
    )
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda *_args: False)
    transaction = root / "su_supervisor" / "transaction"
    transaction.mkdir()
    monkeypatch.setattr(supervisor, "_prepare_install", lambda *_args: str(transaction))
    monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda path: path)
    monkeypatch.setattr(
        supervisor,
        "_activate",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not activate")),
    )

    assert run_supervisor(
        "su_supervisor", state_root=root, installer_sha256=installer_sha256
    ) == 1
    assert store.load("su_supervisor").state.phase is UpdatePhase.ABORTED
    assert store.load("su_supervisor").state.detail["transaction_dir"] == str(transaction)
    assert (root / "maintenance.json").exists() is False


@pytest.mark.parametrize(("failed_stage", "restoration"), [
    (stage, restore) for stage in ("activate", "system") for restore in ("ok", "installer_failed", "probe_failed")
] + [("preflight", "ok"), ("expired", "ok")])
def test_activation_failure_restores_before_reporting_rollback(tmp_path, monkeypatch, failed_stage, restoration):
    from openprogram import paths
    from openprogram.self_update import supervisor, system_probe

    profile = tmp_path / "profile"
    store = _staging(profile / "self-updates", timeout_seconds=1 if failed_stage == "expired" else 1800)
    digest = _installer(store.root)
    update_dir = store.root / "su_supervisor"
    tx = update_dir / "transaction"
    tx.mkdir()
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr(supervisor, "_build_candidate", lambda *_: Artifact(update_dir, "a" * 64))
    monkeypatch.setattr(supervisor, "_prepare_install", lambda *_: str(tx))
    # This test isolates activation/system failure and restoration, not packaging.
    monkeypatch.setattr(supervisor, "_prepare_reopen_activation", lambda *_: None)
    monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda path: path)
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda *_: True)
    old = {"candidate_sha": "3" * 40, "worker_pid": 1234}
    restored = {**old, "worker_pid": 5678}
    def old_probe(*_):
        if failed_stage == "preflight":
            raise system_probe.SystemProbeError("system probe failed: identity")
        return old
    monkeypatch.setattr(system_probe, "probe_current_system", old_probe)
    calls = []
    def activate(*_):
        calls.append("activate")
        if failed_stage == "expired":
            time.sleep(1.1)
            raise RuntimeError("activation timed out")
        if failed_stage == "activate":
            raise RuntimeError("activation failed")
        return str(tx)
    monkeypatch.setattr(supervisor, "_activate", activate)
    def probe(*_):
        raise system_probe.SystemProbeError("system probe failed: doctor")
    monkeypatch.setattr(system_probe, "probe_system", probe)
    def installer(argument, directory, sha, mode):
        assert mode == "--rollback" and argument == tx and directory == update_dir and sha == digest
        assert (update_dir / "rollback-1.json").is_file()
        calls.append("rollback")
        if restoration == "installer_failed":
            raise RuntimeError("installer refused")
        return str(tx)
    monkeypatch.setattr(supervisor, "_installer_command", installer)
    def restored_probe(record, revision):
        assert revision == "3" * 40 != record.request.base_sha
        calls.append("restored_probe")
        if restoration == "probe_failed":
            raise system_probe.SystemProbeError("system probe failed: identity")
        return restored
    monkeypatch.setattr(system_probe, "probe_restored_system", restored_probe, raising=False)
    assert run_supervisor("su_supervisor", state_root=store.root, installer_sha256=digest) == 1
    state = store.load("su_supervisor").state
    if failed_stage == "preflight":
        assert state.phase is UpdatePhase.ABORTED and calls == []
        assert not (store.root / "maintenance.json").exists()
        return
    assert calls[:2] == ["activate", "rollback"]
    assert state.detail["transaction_dir"] == str(tx)
    if restoration == "ok":
        assert state.phase is UpdatePhase.ROLLED_BACK
        assert state.detail["restored_system_gate"] == restored
    else:
        assert state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY
        assert "recovery_error" in state.detail
    assert (store.root / "maintenance.json").exists() is (restoration != "ok")


def test_quiescence_waits_without_cancelling_sessions_or_jobs(monkeypatch) -> None:
    from openprogram.agent.job import store as job_store
    from openprogram.self_update import supervisor
    from openprogram import store as session_store

    polls = 0

    class Sessions:
        def list_sessions(self, *, status=None, **_kwargs):
            nonlocal polls
            if status == "running":
                polls += 1
                return [{"id": "session-1"}] if polls == 1 else []
            return [{"id": "session-1"}]

    job_polls = 0

    def list_jobs(*_args, **_kwargs):
        nonlocal job_polls
        job_polls += 1
        return [object()] if job_polls == 1 else []

    sessions = Sessions()
    monkeypatch.setattr(session_store, "default_store", lambda: sessions)
    monkeypatch.setattr(job_store, "list_jobs", list_jobs)
    monkeypatch.setattr(supervisor.time, "sleep", lambda _seconds: None)

    assert supervisor._wait_for_quiescence(supervisor.time.time() + 1) is True
    assert polls == 2
    assert job_polls == 2


def test_build_runs_fixed_entry_in_private_network_denied_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.programs.tools.system import self_update as tool_module
    from openprogram.self_update import supervisor
    from openprogram.worktree import manager

    root = tmp_path / "profile" / "self-updates"
    store = _staging(root)
    record = store.load("su_supervisor")
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    script = candidate / "apps/desktop/scripts/package-and-install-app.sh"
    source.mkdir()
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    worktree = SimpleNamespace(
        source_repo=str(source),
        worktree_path=str(candidate),
        branch_name="codex/candidate",
        parent_session="session-1",
    )
    monkeypatch.setattr(
        manager,
        "get_manager",
        lambda: SimpleNamespace(get_worktree=lambda _worktree_id: worktree),
    )
    monkeypatch.setattr(tool_module, "_recorded_path", lambda value, _name: Path(value))
    validations: list[str] = []
    monkeypatch.setattr(
        tool_module,
        "_validate_registered_worktree",
        lambda *_args: validations.append("registered"),
    )
    monkeypatch.setattr(
        tool_module,
        "_validate_candidate_snapshot",
        lambda *_args: validations.append("snapshot"),
    )
    monkeypatch.setattr(
        supervisor, "_sandbox_executable", lambda: Path("/usr/bin/sandbox-exec")
    )
    calls: list[dict] = []

    def run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        output = Path(args[args.index("--output") + 1])
        output.mkdir(parents=True)
        (output / "artifact.txt").write_text("candidate", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "built\n", "")

    monkeypatch.setattr(supervisor.subprocess, "run", run)
    monkeypatch.setattr(
        "openprogram.self_update.controller_bundle.build_inputs",
        lambda *_args, **_kwargs: nullcontext({"UV_OFFLINE": "1"}),
    )

    artifact = supervisor._build_candidate(record, root / "su_supervisor")

    call = calls[0]
    assert call["args"][:4] == [
        "/usr/bin/sandbox-exec",
        "-f",
        str(root / "su_supervisor" / "sandbox.sb"),
        "/bin/bash",
    ]
    assert call["args"][4:7] == [str(script), "--output", str(artifact.path)]
    assert call["cwd"] == str(candidate)
    assert set(call["env"]) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "CI",
        "NPM_CONFIG_USERCONFIG",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "UV_OFFLINE",
    }
    profile = (root / "su_supervisor" / "sandbox.sb").read_text(encoding="utf-8")
    assert "(deny network*)" in profile
    assert str(tmp_path / "profile") in profile
    assert validations == ["registered", "snapshot", "registered", "snapshot"]
    assert (root / "su_supervisor" / "artifact.json").is_file()


@pytest.mark.macos
def test_candidate_sandbox_reads_platform_without_allowing_external_writes(tmp_path: Path) -> None:
    from openprogram.self_update import supervisor

    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file():
        pytest.skip("requires macOS sandbox-exec")
    candidate, artifact, build_home, build_tmp = (
        tmp_path / name for name in ("candidate", "artifact", "home", "tmp")
    )
    for directory in (candidate, artifact, build_home, build_tmp):
        directory.mkdir()
    prefix = [str(sandbox), "-p", supervisor._sandbox_profile(candidate, artifact, build_home, build_tmp)]
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(build_home), "TMPDIR": str(build_tmp)}
    platform = subprocess.run(
        [*prefix, "/usr/bin/uname", "-s"], env=environment,
        capture_output=True, text=True, timeout=10,
    )
    assert platform.returncode == 0, platform.stderr
    assert platform.stdout.strip() == "Darwin"
    node = shutil.which("node", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    assert node is not None
    host = subprocess.run(
        [*prefix, node, "-e", "const o=require('os');console.log(JSON.stringify({arch:o.arch(),models:o.cpus().map(c=>c.model)}))"],
        env=environment, capture_output=True, text=True, timeout=10,
    )
    assert host.returncode == 0, host.stderr
    observed = json.loads(host.stdout)
    assert observed["arch"] == "arm64"
    assert observed["models"] and all(value.startswith("Apple ") for value in observed["models"])
    for target, allowed in ((artifact / "own-output", True), (tmp_path / "outside-output", False)):
        result = subprocess.run(
            [*prefix, "/usr/bin/touch", str(target)], env=environment,
            capture_output=True, text=True, timeout=10,
        )
        assert (result.returncode == 0) is allowed, result.stderr
        assert target.exists() is allowed


@pytest.mark.macos
def test_candidate_sandbox_cannot_read_installed_app(tmp_path: Path) -> None:
    from openprogram.self_update import supervisor

    sandbox = Path("/usr/bin/sandbox-exec")
    installed_app = Path("/Applications/OpenProgram.app")
    if not sandbox.is_file() or not installed_app.is_dir():
        pytest.skip("requires macOS sandbox-exec and the canonical installed App")
    candidate = tmp_path / "candidate"
    artifact_root = tmp_path / "update" / "artifact"
    build_home = tmp_path / "update" / "build-home"
    build_tmp = tmp_path / "update" / "build-tmp"
    for directory in (candidate, artifact_root, build_home, build_tmp):
        directory.mkdir(parents=True, exist_ok=True)
    profile = tmp_path / "update" / "sandbox.sb"
    profile.write_text(
        supervisor._sandbox_profile(
            candidate, artifact_root, build_home, build_tmp
        ),
        encoding="utf-8",
    )
    readable = artifact_root / "install-dir-readable"
    allowed = artifact_root / "staging-write-allowed"
    command = (
        f'/bin/ls "{installed_app}" >/dev/null 2>&1 '
        f'&& /usr/bin/touch "{readable}"; '
        f'/usr/bin/touch "{allowed}"'
    )

    result = subprocess.run(
        [str(sandbox), "-f", str(profile), "/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(build_home),
            "TMPDIR": str(build_tmp),
        },
    )

    assert result.returncode == 0, result.stderr
    assert readable.exists() is False
    assert allowed.is_file()


def test_artifact_digest_rejects_symlink_outside_bundle(tmp_path: Path) -> None:
    from openprogram.self_update import supervisor

    app = tmp_path / "OpenProgram.app"
    app.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("secret", encoding="utf-8")
    (app / "escape").symlink_to(outside)

    with pytest.raises(RuntimeError, match="escapes App bundle"):
        supervisor._tree_digest(app)


def test_controller_lock_does_not_follow_symlink(tmp_path: Path) -> None:
    from openprogram.self_update import supervisor

    outside = tmp_path / "outside"
    outside.write_text("unchanged", encoding="utf-8")
    (tmp_path / "supervisor.lock").symlink_to(outside)

    with pytest.raises(OSError):
        with supervisor._controller_lock(tmp_path):
            pass
    assert outside.read_text(encoding="utf-8") == "unchanged"


def test_supervisor_rejects_update_id_before_using_path(tmp_path, monkeypatch) -> None:
    from openprogram import paths

    profile = tmp_path / "profile"
    root = profile / "self-updates"
    root.mkdir(parents=True)
    outside = profile / "evil"
    outside.mkdir()
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)

    with pytest.raises(ValueError, match="update_id"):
        run_supervisor("../evil", state_root=root, installer_sha256="a" * 64)
    assert not (outside / "supervisor.lock").exists()


def test_supervisor_rejects_symlink_update_directory(tmp_path, monkeypatch) -> None:
    from openprogram import paths

    profile = tmp_path / "profile"
    root = profile / "self-updates"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "su_alias").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)

    with pytest.raises(ValueError, match="real private directory"):
        run_supervisor("su_alias", state_root=root, installer_sha256="a" * 64)
    assert not (outside / "supervisor.lock").exists()


def test_prepare_uses_hash_pinned_snapshot_and_prepare_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.self_update import supervisor

    update_dir = tmp_path / "su_prepare"
    app = update_dir / "artifact" / "OpenProgram.app"
    app.mkdir(parents=True)
    (app / "content").write_text("candidate", encoding="utf-8")
    artifact = Artifact(app, supervisor._tree_digest(app))
    installer_sha256 = _installer_at(update_dir)
    transaction = "/Applications/.openprogram-app-install.123"
    calls: list[tuple[list[str], dict]] = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args,
            0,
            f"OPENPROGRAM_TRANSACTION_DIR={transaction}\n",
            "",
        )

    monkeypatch.setattr(supervisor.subprocess, "run", run)

    # This existing test isolates command/hash pinning. Real package admission
    # is exercised by test_package_protocol with staged ASAR/runtime fixtures.
    monkeypatch.setattr(supervisor, "validate_reopen_package", lambda _app: {
        "bindings": {"installer": {"sha256": installer_sha256}},
    })
    assert supervisor._prepare_install(artifact, update_dir, installer_sha256) == transaction
    assert calls[0][0] == [
        "/bin/bash",
        str(update_dir / "controller" / "install-app.sh"),
        "--reopen-update=su_prepare",
        "--prepare",
        str(app),
    ]
    assert set(calls[0][1]["env"]) == {"PATH", "HOME"}


def test_prepare_rejects_missing_reopen_protocol_before_installer(tmp_path, monkeypatch):
    from openprogram.self_update import supervisor

    update_dir = tmp_path / "su_protocol"
    app = update_dir / "artifact" / "OpenProgram.app"
    app.mkdir(parents=True)
    artifact = Artifact(app, supervisor._tree_digest(app))
    digest = _installer_at(update_dir)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "OPENPROGRAM_TRANSACTION_DIR=/Applications/.openprogram-app-install.fixture\n", "")
    monkeypatch.setattr(supervisor.subprocess, "run", run)
    with pytest.raises(ValueError, match="reopen protocol"):
        supervisor._prepare_install(artifact, update_dir, digest)
    assert calls == []


def test_prepare_rejects_installer_or_artifact_drift(tmp_path: Path) -> None:
    from openprogram.self_update import supervisor

    update_dir = tmp_path / "update"
    app = update_dir / "artifact" / "OpenProgram.app"
    app.mkdir(parents=True)
    content = app / "content"
    content.write_text("candidate", encoding="utf-8")
    artifact = Artifact(app, supervisor._tree_digest(app))
    installer_sha256 = _installer_at(update_dir)
    (update_dir / "controller" / "install-app.sh").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="installer snapshot changed"):
        supervisor._prepare_install(artifact, update_dir, installer_sha256)

    _installer_at(update_dir)
    content.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="artifact changed"):
        supervisor._prepare_install(artifact, update_dir, installer_sha256)


def test_activate_uses_only_the_prepared_transaction(tmp_path, monkeypatch):
    from openprogram.self_update import supervisor
    update_dir = tmp_path / "su_activate"
    digest = _installer_at(update_dir)
    tx = tmp_path / "transaction"
    tx.mkdir()
    SelfUpdateStore(tmp_path / "store")._write_json(tx / "transaction.json", dict(
        schema=1, phase="prepared", previous_sha256="a" * 64, active_sha256="b" * 64,
        app=False, worker=False, launchd=False, reopen_update_id=update_dir.name))
    monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda path: path)
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, f"OPENPROGRAM_TRANSACTION_DIR={tx}\n", "")
    monkeypatch.setattr(supervisor.subprocess, "run", run)
    assert supervisor._activate(tx, update_dir, digest) == str(tx)
    assert calls[0][0] == ["/bin/bash", str(update_dir / "controller/install-app.sh"), "--activate", str(tx)]
    assert set(calls[0][1]["env"]) == {"PATH", "HOME"}


def _installer_at(update_dir: Path) -> str:
    path = update_dir / "controller" / "install-app.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()
