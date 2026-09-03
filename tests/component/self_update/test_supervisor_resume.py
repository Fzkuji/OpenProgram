"""Controller process loss and fail-closed re-entry at durable phases."""
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from openprogram.self_update import UpdatePhase
from openprogram.self_update import supervisor
from tests.component.self_update.test_supervisor import _staging, _installer


@pytest.fixture
def staged(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    store = _staging(profile / "self-updates")
    sha = _installer(store.root)
    update_dir = store.root / "su_supervisor"
    artifact = update_dir / "artifact/OpenProgram.app"
    artifact.mkdir(parents=True)
    (artifact / "content").write_text("candidate")
    transaction = update_dir / "transaction"
    transaction.mkdir()
    detail = {"artifact_path": str(artifact), "artifact_sha256": supervisor._tree_digest(artifact),
              "transaction_dir": str(transaction), "previous_system_gate": {"candidate_sha": "3" * 40}}
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: profile)
    monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda path: path)
    def forbidden(*args):
        pytest.fail("resumption must not rebuild, re-prepare or activate an invalid candidate")
    monkeypatch.setattr(supervisor, "_build_candidate", forbidden)
    monkeypatch.setattr(supervisor, "_prepare_install", forbidden)
    monkeypatch.setattr(supervisor, "_activate", forbidden)
    return SimpleNamespace(profile=profile, store=store, sha=sha, update_dir=update_dir,
                           artifact=artifact, transaction=transaction, detail=detail)


def run(v):
    return supervisor.run_supervisor("su_supervisor", state_root=v.store.root, installer_sha256=v.sha)


@pytest.mark.parametrize("mutation", ["changed", "missing", "symlink"])
def test_ready_reentry_rejects_changed_artifact_before_activation(staged, mutation):
    v = staged
    v.store.transition("su_supervisor", UpdatePhase.READY, detail=v.detail)
    supervisor.enter_maintenance("su_supervisor")
    if mutation == "changed":
        (v.artifact / "content").write_text("changed")
    else:
        original = v.artifact.with_name("retained-original")
        v.artifact.rename(original)
        if mutation == "symlink":
            v.artifact.symlink_to(original, target_is_directory=True)
    assert run(v) == 1
    state = v.store.load("su_supervisor").state
    assert state.phase is UpdatePhase.ABORTED
    assert "artifact changed" in state.detail["error"]
    assert v.transaction.is_dir()
    assert not (v.store.root / "maintenance.json").exists()


def test_real_controller_sigkill_releases_lock_and_preserves_ready(staged, monkeypatch):
    v = staged
    code = """
import os, signal, sys
from pathlib import Path
from openprogram import paths
from openprogram.self_update import supervisor, SelfUpdateStore, UpdatePhase
profile = Path(sys.argv[1])
paths.get_state_dir = lambda: profile
def interrupted_build(record, update_dir):
    artifact = update_dir / 'artifact/OpenProgram.app'
    SelfUpdateStore().transition(record.request.update_id, UpdatePhase.READY, detail={
        'artifact_path': str(artifact), 'artifact_sha256': supervisor._tree_digest(artifact),
        'transaction_dir': str(update_dir / 'transaction')})
    os.kill(os.getpid(), signal.SIGKILL)
supervisor._build_candidate = interrupted_build
supervisor.run_supervisor('su_supervisor', state_root=profile / 'self-updates', installer_sha256=sys.argv[2])
"""
    result = subprocess.run([sys.executable, "-c", code, str(v.profile), v.sha],
                            cwd=Path(__file__).resolve().parents[3], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == -signal.SIGKILL, result.stderr
    assert v.store.load("su_supervisor").state.phase is UpdatePhase.READY
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda _: False)
    assert run(v) == 1
    state = v.store.load("su_supervisor").state
    assert state.phase is UpdatePhase.ABORTED
    assert state.detail["error"] == "quiescence timed out"
    assert not (v.store.root / "maintenance.json").exists()


def test_ready_reentry_keeps_original_quiescence_deadline(staged, monkeypatch):
    v = staged
    state = v.store.transition("su_supervisor", UpdatePhase.READY, detail=v.detail)
    monkeypatch.setattr(supervisor, "time", SimpleNamespace(time=lambda: state.updated_at + 300))
    deadlines = []
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda deadline: deadlines.append(deadline) or False)
    assert run(v) == 1
    assert deadlines == [state.updated_at + 600]


@pytest.mark.parametrize("restored", [True, False])
def test_invalid_verifying_grant_restores_or_retains_maintenance(staged, monkeypatch, restored):
    v = staged
    v.store.transition("su_supervisor", UpdatePhase.READY, detail=v.detail)
    supervisor.enter_maintenance("su_supervisor")
    for phase in (UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING):
        v.store.transition("su_supervisor", phase, detail=v.detail)
    commands = []
    def installer(argument, directory, digest, mode):
        commands.append(mode)
        assert (argument, directory, digest) == (v.transaction, v.update_dir, v.sha)
        assert (v.update_dir / "rollback-1.json").exists()
        return str(v.transaction)
    monkeypatch.setattr(supervisor, "_installer_command", installer)
    def probe(record, revision):
        assert revision == "3" * 40
        if not restored:
            raise RuntimeError("restored worker unavailable")
        return {"candidate_sha": revision}
    monkeypatch.setattr("openprogram.self_update.system_probe.probe_restored_system", probe)
    assert run(v) == 1
    state = v.store.load("su_supervisor").state
    assert commands == ["--rollback"]
    assert state.phase is (UpdatePhase.ROLLED_BACK if restored else UpdatePhase.NEEDS_MANUAL_RECOVERY)
    assert (v.store.root / "maintenance.json").exists() is not restored


def test_resumed_rollback_does_not_extend_an_expired_deadline(staged, monkeypatch):
    from openprogram.self_update.rollback_intent import begin_rollback, load_rollback_intent
    v = staged
    v.store.transition("su_supervisor", UpdatePhase.READY, detail=v.detail)
    supervisor.enter_maintenance("su_supervisor")
    v.store.transition("su_supervisor", UpdatePhase.ACTIVATING, detail=v.detail)
    intent = begin_rollback(v.store, "su_supervisor", "original failure")
    monkeypatch.setattr(supervisor, "time", SimpleNamespace(time=lambda: intent["deadline"] + 1))
    def forbidden(*args):
        pytest.fail("expired recovery must not start an installer")
    monkeypatch.setattr(supervisor, "_installer_command", forbidden)
    assert run(v) == 1
    record = v.store.load("su_supervisor")
    assert record.state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY
    assert load_rollback_intent(v.store, record) == intent
    assert (v.store.root / "maintenance.json").exists()
