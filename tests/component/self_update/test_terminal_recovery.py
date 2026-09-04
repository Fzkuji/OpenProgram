"""Recovery after terminal state persistence but before maintenance removal."""
import json

import pytest

from openprogram.self_update import UpdatePhase, supervisor
from openprogram.self_update.maintenance import maintenance_blocks
from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.self_update.test_system_probe import live  # noqa: F401
from tests.component.self_update.test_verification_channel import verifier  # noqa: F401
from tests.component.self_update.test_install_transaction import installation, phase  # noqa: F401
from tests.component.self_update.test_commit_recovery import accepted, run, interrupt, expire  # noqa: F401
from tests.component.config.test_distribution_release import MACOS_DESKTOP_INSTALL

pytestmark = [pytest.mark.macos, MACOS_DESKTOP_INSTALL]


def terminal(v, monkeypatch, outcome):
    if outcome == "rolled_back":
        interrupt(v, monkeypatch, "before")
        expire(monkeypatch)
    leave = supervisor.leave_maintenance
    def interrupted(_):
        raise SystemExit("before maintenance cleanup")
    monkeypatch.setattr(supervisor, "leave_maintenance", interrupted)
    with pytest.raises(SystemExit, match="before maintenance cleanup"):
        run(v)
    monkeypatch.setattr(supervisor, "leave_maintenance", leave)
    assert v.v.store.load(v.v.request.update_id).state.phase.value == outcome
    assert v.v.store.load_active() is None
    assert maintenance_blocks("web")
    v.commands.clear()


@pytest.mark.parametrize("outcome", ["succeeded", "rolled_back"])
@pytest.mark.parametrize("concurrent", [False, True])
def test_terminal_controller_reentry_clears_only_after_fresh_verification(accepted, monkeypatch, outcome, concurrent):
    v = accepted
    terminal(v, monkeypatch, outcome)
    before = {name: (v.update / name).read_bytes() for name in ("state.json", "events.jsonl", "verifier-grant-1.json")}
    journal = (v.transaction / "transaction.json").read_bytes()
    expire(monkeypatch)
    if concurrent:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda _: run(v), range(2))) == [0, 0]
    else:
        assert run(v) == 0
    assert not maintenance_blocks("web")
    assert all((v.update / name).read_bytes() == content for name, content in before.items())
    assert (v.transaction / "transaction.json").read_bytes() == journal
    assert len(v.v.runner.list_jobs("p1")) == 1
    receipt = json.loads((v.update / "maintenance-cleanup-1.json").read_text())
    assert receipt["phase"] == outcome and receipt["system_gate"]["checks"]["doctor"] is True
    assert v.commands == ["--verify-terminal:committed" if outcome == "succeeded" else "--verify-terminal:rolled_back"] * 2
    assert run(v) == 0  # No new probe or installer command after cleanup.
    assert len(v.commands) == 2


@pytest.mark.parametrize("damage", ["decision", "journal", "app", "doctor", "owner", "evidence_during_probe"])
def test_terminal_cleanup_preserves_maintenance_on_changed_evidence(accepted, monkeypatch, damage):
    from openprogram.self_update import system_probe
    v = accepted
    terminal(v, monkeypatch, "succeeded")
    state = (v.update / "state.json").read_bytes()
    if damage == "decision":
        (v.update / "commit-1.json").rename(v.update / "retained-commit.json")
    elif damage == "journal":
        value = json.loads((v.transaction / "transaction.json").read_text())
        value["phase"] = "committing"
        v.v.store._write_json(v.transaction / "transaction.json", value)
    elif damage == "app":
        (v.target / "drift").write_text("changed")
    elif damage == "doctor":
        v.v.flags["doctor"] = False
    elif damage == "owner":
        value = json.loads((v.v.store.root / "maintenance.json").read_text())
        value["update_id"] = "su_another"
        v.v.store._write_json(v.v.store.root / "maintenance.json", value)
    else:
        probe = system_probe.probe_committed_system
        def changed(record):
            result = probe(record)
            (v.update / "commit-1.json").rename(v.update / "retained-commit.json")
            return result
        monkeypatch.setattr(system_probe, "probe_committed_system", changed)
    assert run(v) == 1
    assert maintenance_blocks("web")
    assert (v.update / "state.json").read_bytes() == state
    assert (v.update / "maintenance-error-1.json").is_file()
    assert all(command.startswith("--verify-terminal:") for command in v.commands)


def test_manual_recovery_is_not_automatically_cleared(accepted, monkeypatch):
    v = accepted
    v.v.store.transition(v.v.request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY)
    assert run(v) == 1
    assert maintenance_blocks("web")
    assert v.commands == []


def test_aborted_prepared_transaction_recovers_without_activation(live, installation, monkeypatch):
    import hashlib
    import os
    from openprogram.webui.routes import misc
    from openprogram.self_update import SelfUpdateStore
    from tests.component.self_update.test_install_transaction import INSTALLER, prepare
    record, _, _ = live
    store = SelfUpdateStore()
    native, artifact, target, tmp = installation
    transaction = prepare(native, artifact)
    # Keep the active fixture's event history intact; prepare a separate request.
    from dataclasses import replace
    store.transition(record.request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY)
    request = replace(record.request, update_id="su_aborted")
    store.create(request)
    store.transition(request.update_id, UpdatePhase.STAGING)
    store.transition(request.update_id, UpdatePhase.READY, detail={"transaction_dir": str(transaction)})
    from openprogram.self_update.maintenance import enter_maintenance
    enter_maintenance(request.update_id)
    supervisor._abort(store, request.update_id, UpdatePhase.READY, "quiescence timed out")
    directory = store.root / request.update_id
    installer = directory / "controller/install-app.sh"
    installer.parent.mkdir()
    installer.write_bytes(INSTALLER.read_bytes())
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda value: transaction if value == transaction else pytest.fail("wrong transaction"))
    native_run = supervisor.subprocess.run
    def fixture_run(args, **kwargs):
        if args[:2] == ["/bin/bash", str(installer)]:
            kwargs["env"] = {"DESTDIR": str(target.parent.parent), "HOME": str(tmp / "home"),
                             "TMPDIR": str(tmp / "tmp"), "PATH": os.environ["PATH"]}
        return native_run(args, **kwargs)
    monkeypatch.setattr(supervisor.subprocess, "run", fixture_run)
    monkeypatch.setattr(misc, "_HEAD_SHA", "3" * 40 + "-dirty")
    expire(monkeypatch)
    journal = (transaction / "transaction.json").read_bytes()
    assert supervisor.run_supervisor(request.update_id, state_root=store.root, installer_sha256=digest) == 0
    assert not maintenance_blocks("web")
    assert phase(transaction) == "prepared" and (transaction / "transaction.json").read_bytes() == journal
    assert store.load(request.update_id).state.phase is UpdatePhase.ABORTED
