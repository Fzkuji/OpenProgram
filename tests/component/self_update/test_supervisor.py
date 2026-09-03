from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from openprogram.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.self_update.supervisor import Artifact, run_supervisor


def _staging(root: Path) -> SelfUpdateStore:
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
        )
    )
    store.transition("su_supervisor", UpdatePhase.STAGING)
    return store


def _installer(root: Path) -> str:
    path = root / "su_supervisor" / "install-app.sh"
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
    monkeypatch.setattr(supervisor, "_activate", lambda *_args: str(transaction))
    monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda path: path)

    assert run_supervisor(
        "su_supervisor", state_root=root, installer_sha256=installer_sha256
    ) == 0
    record = store.load("su_supervisor")
    assert record.state.phase is UpdatePhase.VERIFYING
    assert record.state.detail["transaction_dir"] == str(transaction)
    assert record.state.detail["artifact_sha256"] == artifact.sha256
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
    monkeypatch.setattr(
        supervisor,
        "_activate",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not activate")),
    )

    assert run_supervisor(
        "su_supervisor", state_root=root, installer_sha256=installer_sha256
    ) == 1
    assert store.load("su_supervisor").state.phase is UpdatePhase.ABORTED
    assert (root / "maintenance.json").exists() is False


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
    }
    profile = (root / "su_supervisor" / "sandbox.sb").read_text(encoding="utf-8")
    assert "(deny network*)" in profile
    assert str(tmp_path / "profile") in profile
    assert validations == ["registered", "snapshot", "registered", "snapshot"]
    assert (root / "su_supervisor" / "artifact.json").is_file()


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


def test_activate_uses_hash_pinned_snapshot_and_deferred_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.self_update import supervisor

    update_dir = tmp_path / "update"
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

    assert supervisor._activate(artifact, update_dir, installer_sha256) == transaction
    assert calls[0][0] == [
        "/bin/bash",
        str(update_dir / "install-app.sh"),
        "--defer-commit",
        str(app),
    ]
    assert set(calls[0][1]["env"]) == {"PATH", "HOME"}


def test_activate_rejects_installer_or_artifact_drift(tmp_path: Path) -> None:
    from openprogram.self_update import supervisor

    update_dir = tmp_path / "update"
    app = update_dir / "artifact" / "OpenProgram.app"
    app.mkdir(parents=True)
    content = app / "content"
    content.write_text("candidate", encoding="utf-8")
    artifact = Artifact(app, supervisor._tree_digest(app))
    installer_sha256 = _installer_at(update_dir)
    (update_dir / "install-app.sh").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="installer snapshot changed"):
        supervisor._activate(artifact, update_dir, installer_sha256)

    _installer_at(update_dir)
    content.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="artifact changed"):
        supervisor._activate(artifact, update_dir, installer_sha256)


def _installer_at(update_dir: Path) -> str:
    path = update_dir / "install-app.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()
