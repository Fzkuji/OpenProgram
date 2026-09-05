"""Accepted decisions and native irreversible commits survive controller loss."""
import hashlib
import json
import os
import time
from types import SimpleNamespace

import pytest

from openprogram.self_update import UpdatePhase, supervisor
from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.self_update.test_system_probe import live  # noqa: F401
from tests.component.self_update.test_verification_channel import verifier  # noqa: F401
from tests.component.self_update.test_install_transaction import installation, INSTALLER, prepare, phase, version  # noqa: F401
from tests.component.config.test_distribution_release import MACOS_DESKTOP_INSTALL

pytestmark = [pytest.mark.macos, MACOS_DESKTOP_INSTALL]


@pytest.fixture
def accepted(verifier, installation, monkeypatch):
    from openprogram.self_update import verification_channel as channel
    from openprogram.webui.routes import misc

    v = verifier
    install, artifact, target, tmp = installation
    transaction = prepare(install, artifact)
    install("--activate", transaction)
    update = v.store.root / v.request.update_id
    installer = update / "controller/install-app.sh"
    installer.parent.mkdir()
    installer.write_bytes(INSTALLER.read_bytes())
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    state_path = update / "state.json"
    state = json.loads(state_path.read_text())
    state["detail"]["transaction_dir"] = str(transaction)
    v.store._write_json(state_path, state)
    v.store._write_json(v.store.root / "maintenance.json",
                        dict(schema=1, update_id=v.request.update_id, entered_at=time.time()))
    job = v.run()
    assert job.result_text is not None
    receipt = channel.consume_result(v.store, v.request.update_id, v.grant["token"])
    assert receipt["verdict"] == "pass"
    def validate(path):
        assert path == transaction and not path.is_symlink()
        return path
    monkeypatch.setattr(supervisor, "_validate_transaction_path", validate)
    native_run = supervisor.subprocess.run
    extra_env = {}
    def run(args, **kwargs):
        if args[:2] == ["/bin/bash", str(installer)]:
            kwargs["env"] = {**kwargs["env"], "DESTDIR": str(target.parent.parent),
                             "HOME": str(tmp / "home"), "TMPDIR": str(tmp / "tmp"),
                             "PATH": os.environ["PATH"], **extra_env}
        return native_run(args, **kwargs)
    monkeypatch.setattr(supervisor.subprocess, "run", run)
    native_command = supervisor._installer_command
    commands = []
    def command(argument, directory, sha, mode):
        commands.append(mode)
        result = native_command(argument, directory, sha, mode)
        if mode == "--rollback":
            monkeypatch.setattr(misc, "_HEAD_SHA", "3" * 40)
        return result
    monkeypatch.setattr(supervisor, "_installer_command", command)
    return SimpleNamespace(v=v, update=update, digest=digest, transaction=transaction, target=target,
                           tmp=tmp, extra_env=extra_env, command=command, commands=commands)


def run(v):
    return supervisor.run_supervisor(v.v.request.update_id, state_root=v.v.store.root, installer_sha256=v.digest)


def interrupt(v, monkeypatch, when="after"):
    def command(argument, directory, sha, mode):
        if mode == "--commit":
            assert (v.update / "commit-1.json").is_file()
            if when == "before":
                raise SystemExit("controller interrupted")
            try:
                v.command(argument, directory, sha, mode)
            except RuntimeError:
                if when != "partial":
                    raise
                assert phase(v.transaction) == "committing"
            raise SystemExit("controller interrupted")
        return v.command(argument, directory, sha, mode)
    monkeypatch.setattr(supervisor, "_installer_command", command)
    with pytest.raises(SystemExit, match="controller interrupted"):
        run(v)
    monkeypatch.setattr(supervisor, "_installer_command", v.command)
    assert v.v.store.load(v.v.request.update_id).state.phase is UpdatePhase.VERIFYING
    v.commands.clear()


def expire(monkeypatch):
    original = time.time
    monkeypatch.setattr(time, "time", lambda: original() + 4000)


def test_expired_decision_before_native_commit_does_not_authorize_late_deletion(accepted, monkeypatch):
    v = accepted
    interrupt(v, monkeypatch, "before")
    assert phase(v.transaction) == "activated" and (v.transaction / "previous.app").is_dir()
    expire(monkeypatch)
    assert run(v) == 1
    assert v.commands == ["--rollback"]
    assert v.v.store.load(v.v.request.update_id).state.phase is UpdatePhase.ROLLED_BACK
    assert phase(v.transaction) == "rolled_back" and version(v.target) == "0.6.1"


def test_deadline_is_checked_after_durable_decision_before_first_native_commit(accepted, monkeypatch):
    from openprogram.self_update import commit_intent
    v = accepted
    begin = commit_intent.begin_commit
    def delayed(*args):
        result = begin(*args)
        expire(monkeypatch)  # The durable write/validation consumed the remaining window.
        return result
    monkeypatch.setattr(commit_intent, "begin_commit", delayed)
    assert run(v) == 1
    assert v.commands == ["--rollback"]
    assert phase(v.transaction) == "rolled_back" and version(v.target) == "0.6.1"


@pytest.mark.parametrize("damage", ["missing_decision", "decision", "grant", "result", "journal", "symlink"])
def test_irreversible_commit_requires_original_decision_and_bound_evidence(accepted, monkeypatch, damage):
    v = accepted
    interrupt(v, monkeypatch)
    assert phase(v.transaction) == "committed" and not (v.transaction / "previous.app").exists()
    decision = v.update / "commit-1.json"
    if damage in {"missing_decision", "symlink"}:
        saved = decision.with_name("retained-commit.json")
        decision.rename(saved)
        if damage == "symlink":
            decision.symlink_to(saved)
    else:
        path = {"decision": decision, "grant": v.update / "verifier-grant-1.json",
                "result": v.update / "verifier-result-1.json", "journal": v.transaction / "transaction.json"}[damage]
        value = json.loads(path.read_text())
        value[{"decision": "candidate_sha", "grant": "candidate_sha", "result": "verdict", "journal": "active_sha256"}[damage]] = "f" * 64
        v.v.store._write_json(path, value)
    expire(monkeypatch)
    assert run(v) == 1
    assert v.commands == []  # Neither an unproved commit nor impossible rollback.
    assert v.v.store.load(v.v.request.update_id).state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY
    assert (v.v.store.root / "maintenance.json").is_file()
    assert phase(v.transaction) == "committed"


def test_completed_commit_with_failed_fresh_system_probe_keeps_maintenance(accepted, monkeypatch):
    v = accepted
    interrupt(v, monkeypatch)
    v.v.flags["doctor"] = False
    expire(monkeypatch)
    assert run(v) == 1
    assert v.commands == ["--commit"]
    record = v.v.store.load(v.v.request.update_id)
    assert record.state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY
    assert record.state.detail["recovery_error"] == "system probe failed: doctor"
    assert (v.v.store.root / "maintenance.json").is_file()


def test_native_partial_deletion_is_reconciled_after_expiry_without_new_verifier(accepted, monkeypatch):
    v = accepted
    shim = v.tmp / "commit-shim"
    shim.mkdir()
    executable = shim / "rm"
    executable.write_text('#!/bin/bash\nif [[ "${@: -1}" == "$PARTIAL_PREVIOUS" ]]; then\n'
                          '/bin/rm "$PARTIAL_PREVIOUS/Contents/Info.plist" || exit $?\n'
                          'kill -KILL "$PPID"\nelse /bin/rm "$@"; fi\n')
    executable.chmod(0o755)
    v.extra_env.update(PATH=str(shim) + ":" + os.environ["PATH"], PARTIAL_PREVIOUS=str(v.transaction / "previous.app"))
    interrupt(v, monkeypatch, "partial")  # Native installer SIGKILL, controller SystemExit.
    assert phase(v.transaction) == "committing"
    assert (v.transaction / "previous.app").is_dir()
    assert not (v.transaction / "previous.app/Contents/Info.plist").exists()
    decision = (v.update / "commit-1.json").read_bytes()
    grant = (v.update / "verifier-grant-1.json").read_bytes()
    v.extra_env.clear()
    expire(monkeypatch)
    assert run(v) == 0
    assert v.commands == ["--commit"]
    assert phase(v.transaction) == "committed" and version(v.target) == "0.6.2"
    assert (v.update / "commit-1.json").read_bytes() == decision
    assert (v.update / "verifier-grant-1.json").read_bytes() == grant
    assert len(v.v.runner.list_jobs("p1")) == 1
    assert not (v.v.store.root / "maintenance.json").exists()
