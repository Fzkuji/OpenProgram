"""Controller-to-native-installer recovery handoff using fixture Apps only."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from openprogram import paths
from openprogram.agent import authority
from openprogram.self_update import SelfUpdateStore, UpdatePhase
from openprogram.self_update import supervisor
from openprogram.self_update.commit_intent import read_journal
from openprogram.self_update.verifier_config import config_evidence, freeze_verifier_config
from tests.component.self_update.test_package_protocol import package_factory  # noqa: F401
from tests.component.self_update.test_install_transaction import INSTALLER, version
from tests.component.config.test_distribution_release import MACOS_DESKTOP_INSTALL
from tests.unit.self_update.test_store import _request

pytestmark = [pytest.mark.macos, MACOS_DESKTOP_INSTALL]


@pytest.fixture
def producer(package_factory, tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed"))
    request = _request()
    config = freeze_verifier_config(request, SimpleNamespace(
        profile_snapshot={"id": "main"}, **authority.local_owner_authority()))
    request = replace(request, pre_update_evidence=(*request.pre_update_evidence, config_evidence(config)))
    store = SelfUpdateStore(profile / "self-updates")
    store.create(request, verifier_config=config)
    store.transition(request.update_id, UpdatePhase.STAGING)
    directory = store.root / request.update_id
    installer = directory / "controller/install-app.sh"
    installer.parent.mkdir()
    shutil.copyfile(INSTALLER, installer)
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    old = package_factory("old", version="0.6.1")
    candidate = package_factory("candidate")
    artifact = directory / "artifact/OpenProgram.app"
    shutil.copytree(candidate, artifact)
    artifact = supervisor.Artifact(artifact, supervisor._tree_digest(artifact))
    env = {"DESTDIR": str(tmp_path / "root"), "HOME": str(tmp_path / "home"),
           "TMPDIR": str(tmp_path / "tmp"), "PATH": os.environ["PATH"]}
    Path(env["TMPDIR"]).mkdir()
    subprocess.run(["bash", str(INSTALLER), str(old)], env=env, capture_output=True,
                   text=True, check=True, timeout=30)
    target = tmp_path / "root/Applications/OpenProgram.app"
    monkeypatch.setattr(supervisor, "DEFAULT_APP_PATH", str(target))
    monkeypatch.setattr(supervisor, "_build_candidate", lambda *_: artifact)
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda *_: True)
    monkeypatch.setattr("openprogram.self_update.system_probe.probe_current_system",
                        lambda *_: {"candidate_sha": request.base_sha})
    monkeypatch.setattr("openprogram.self_update.system_probe.probe_system",
                        lambda *_: {"candidate_sha": request.candidate_sha, "worker_pid": os.getpid()})
    monkeypatch.setattr(supervisor, "_finish_verification", lambda *_: 0)
    def validate(path):
        assert path.parent == target.parent and path.name.startswith(".openprogram-app-install.")
        assert path.is_dir() and not path.is_symlink()
        return path
    monkeypatch.setattr(supervisor, "_validate_transaction_path", validate)
    native_run = supervisor.subprocess.run
    activations = []
    def run(args, **kwargs):
        if args[:2] == ["/bin/bash", str(installer)]:
            kwargs["env"] = env
            if "--activate" in args:
                current = store.load(request.update_id)
                assert current.state.phase is UpdatePhase.ACTIVATING
                intent = json.loads((directory / "reopen-1.json").read_text())
                journal = read_journal(Path(args[-1]))
                assert intent["update_id"] == journal["reopen_update_id"] == request.update_id
                assert intent["session_id"] == request.session_id
                assert intent["owner_principal_id"] == authority.owner_principal_id()
                activations.append(intent)
        return native_run(args, **kwargs)
    monkeypatch.setattr(supervisor.subprocess, "run", run)
    return store, directory, digest, target, artifact, activations


@pytest.mark.parametrize("restart", [False, True])
def test_controller_persists_reopen_before_native_activation(producer, monkeypatch, restart):
    store, directory, digest, target, artifact, activations = producer
    if restart:
        original = SelfUpdateStore.transition
        def transition(self, update_id, phase, **kwargs):
            state = original(self, update_id, phase, **kwargs)
            if phase is UpdatePhase.READY:
                raise SystemExit("controller interrupted")
            return state
        with monkeypatch.context() as patch:
            patch.setattr(SelfUpdateStore, "transition", transition)
            with pytest.raises(SystemExit, match="controller interrupted"):
                supervisor.run_supervisor(directory.name, state_root=store.root, installer_sha256=digest)
        assert version(target) == "0.6.1" and activations == []
        from openprogram.self_update.reopen import prepare_reopen
        prepare_reopen(store, directory.name)
        intent_path = directory / "reopen-1.json"
        intent_before = intent_path.read_bytes(), intent_path.stat().st_mtime_ns
        monkeypatch.setattr(supervisor, "_build_candidate", lambda *_: pytest.fail("must resume prepared candidate"))
    assert supervisor.run_supervisor(directory.name, state_root=store.root, installer_sha256=digest) == 0
    assert len(activations) == 1
    assert store.load(directory.name).state.phase is UpdatePhase.VERIFYING
    assert version(target) == "0.6.2"
    if restart:
        assert (intent_path.read_bytes(), intent_path.stat().st_mtime_ns) == intent_before
    tx = Path(store.load(directory.name).state.detail["transaction_dir"])
    assert read_journal(tx)["reopen_update_id"] == directory.name
    # The same native transaction retains its opaque ID when recovering old App.
    supervisor._installer_command(tx, directory, digest, "--rollback")
    assert version(target) == "0.6.1"
    assert read_journal(tx)["reopen_update_id"] == directory.name


@pytest.mark.parametrize("damage,reason", [
    ("foreign_id", "does not match the reopen update"),
    ("legacy_id", "does not match the reopen update"),
    ("invalid_id", "update_id must match"),
    ("installed_protocol", "reopen protocol"),
    ("candidate_drift", "artifact changed"),
    ("owner_config", "verifier configuration is missing"),
    ("intent", "intent_invalid"),
])
def test_ready_reentry_refuses_invalid_handoff_before_stopping_app(producer, damage, reason):
    store, directory, digest, target, artifact, activations = producer
    tx = Path(supervisor._prepare_install(artifact, directory, digest))
    detail = dict(artifact_path=str(artifact.path), artifact_sha256=artifact.sha256, transaction_dir=str(tx))
    store.transition(directory.name, UpdatePhase.READY, detail=detail)
    if damage in {"foreign_id", "legacy_id", "invalid_id"}:
        journal = read_journal(tx)
        if damage == "legacy_id":
            del journal["reopen_update_id"]
        else:
            journal["reopen_update_id"] = "su_other" if damage == "foreign_id" else "file:///other"
        store._write_json(tx / "transaction.json", journal)
    elif damage == "installed_protocol":
        (target / "Contents/Resources/update/reopen-protocol.json").unlink()
    elif damage == "candidate_drift":
        (artifact.path / "changed").write_text("changed")
    elif damage == "owner_config":
        (directory / "verifier-config.json").unlink()
    else:
        from openprogram.self_update.reopen import prepare_reopen
        intent = prepare_reopen(store, directory.name)
        store._write_json(directory / "reopen-1.json", {**intent, "session_id": "other"})
    before = supervisor._tree_digest(target), supervisor._tree_digest(tx)
    assert supervisor.run_supervisor(directory.name, state_root=store.root, installer_sha256=digest) == 1
    state = store.load(directory.name).state
    assert state.phase is UpdatePhase.ABORTED
    assert reason in state.detail["error"]
    assert activations == [] and version(target) == "0.6.1"
    assert (supervisor._tree_digest(target), supervisor._tree_digest(tx)) == before
    assert not (store.root / "maintenance.json").exists()


@pytest.mark.parametrize("update_id", [None, "su_valid", True, "", "su_other/path"])
def test_shared_journal_reader_preserves_legacy_and_validates_new_identity(tmp_path, update_id):
    tx = tmp_path / "transaction"
    tx.mkdir()
    data = dict(schema=1, phase="prepared", previous_sha256="a" * 64,
                active_sha256="b" * 64, app=False, worker=False, launchd=False)
    if update_id is not None:
        data["reopen_update_id"] = update_id
    SelfUpdateStore(tmp_path / "updates")._write_json(tx / "transaction.json", data)
    if update_id in (None, "su_valid"):
        assert read_journal(tx) == data
    else:
        with pytest.raises(ValueError):
            read_journal(tx)
