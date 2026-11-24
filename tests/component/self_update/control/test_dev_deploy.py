"""The development-instance controller, driven against a real Git repository."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.operations.self_update.control import backend, dev_deploy
from openprogram.operations.self_update.control.dev_deploy import LandFailed, land
from openprogram.operations.self_update.verification import system_probe
from openprogram.operations.worker import dev_instance


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo, name, text, message=None):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message or f"edit {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    base = _commit(repo, "base.txt", "base\n")
    tree = tmp_path / "candidate"
    _git(repo, "worktree", "add", "-q", "-b", "op/wt/one", str(tree), base)
    return repo, tree, base


# Landing

def test_a_commit_on_top_of_dev_fast_forwards(repos):
    repo, tree, _ = repos
    head = _commit(tree, "a.txt", "a\n")
    assert land(repo, tree) == head
    assert _git(repo, "rev-parse", "dev") == head


def test_landing_rebases_when_dev_moved(repos):
    repo, tree, _ = repos
    _commit(tree, "a.txt", "a\n")
    other = _commit(repo, "b.txt", "b\n")
    landed = land(repo, tree)
    assert landed != other
    assert _git(repo, "rev-parse", "dev") == landed
    assert _git(repo, "merge-base", "--is-ancestor", other, landed) == ""


def test_landing_is_idempotent(repos):
    repo, tree, _ = repos
    head = _commit(tree, "a.txt", "a\n")
    land(repo, tree)
    assert land(repo, tree) == head


def test_a_rebase_conflict_names_the_files_and_leaves_the_worktree_clean(repos):
    repo, tree, _ = repos
    _commit(tree, "same.txt", "mine\n")
    _commit(repo, "same.txt", "theirs\n")
    with pytest.raises(LandFailed) as failure:
        land(repo, tree)
    assert failure.value.files == ("same.txt",)
    assert _git(tree, "status", "--porcelain") == ""
    assert not Path(_git(tree, "rev-parse", "--git-path", "rebase-merge")).exists()


def test_a_dirty_file_that_overlaps_names_the_file(repos):
    repo, tree, _ = repos
    _commit(tree, "a.txt", "a\n")
    (repo / "a.txt").write_text("someone's unsaved work\n")
    with pytest.raises(LandFailed) as failure:
        land(repo, tree)
    assert "a.txt" in failure.value.files
    assert (repo / "a.txt").read_text() == "someone's unsaved work\n"


def test_a_dirty_file_elsewhere_does_not_stop_the_landing(repos):
    repo, tree, _ = repos
    head = _commit(tree, "a.txt", "a\n")
    (repo / "base.txt").write_text("someone's unsaved work\n")
    assert land(repo, tree) == head


def test_a_held_index_lock_is_waited_out(repos):
    repo, tree, _ = repos
    head = _commit(tree, "a.txt", "a\n")
    lock = repo / ".git" / "index.lock"
    lock.write_text("")
    pauses = []

    def pause(seconds):
        pauses.append(seconds)
        lock.unlink()

    assert land(repo, tree, pause=pause) == head
    assert pauses == [2]


def test_a_branch_that_keeps_moving_gives_up(repos):
    repo, tree, _ = repos
    _commit(tree, "a.txt", "a\n")
    lock = repo / ".git" / "index.lock"
    lock.write_text("")
    with pytest.raises(LandFailed, match="kept moving or stayed locked"):
        land(repo, tree, pause=lambda _: None, attempts=3)


def test_the_primary_checkout_must_be_on_dev(repos):
    repo, tree, base = repos
    _commit(tree, "a.txt", "a\n")
    _git(repo, "checkout", "-q", "--detach", base)
    with pytest.raises(LandFailed, match="not on dev"):
        land(repo, tree)


# The controller's environment

def test_the_controller_never_inherits_a_session_or_a_profile():
    environment = dev_deploy.controller_environment("/repo", {
        "HOME": "/h", "PATH": "/custom/bin", "OPENPROGRAM_SESSION_ID": "s1", "OPENPROGRAM_PROFILE": "dev",
        "ANTHROPIC_API_KEY": "k", "PYTHONPATH": "/x",
    })
    assert "OPENPROGRAM_SESSION_ID" not in environment and "OPENPROGRAM_PROFILE" not in environment
    assert "ANTHROPIC_API_KEY" not in environment and "PYTHONPATH" not in environment
    assert environment["PATH"].split(":")[0] == "/custom/bin"
    assert environment["OPENPROGRAM_SOURCE_REPO"] == "/repo"


# Backend choice

def test_backend_order(tmp_path, monkeypatch, repos):
    repo, _, _ = repos
    plain = tmp_path / "plain"
    plain.mkdir()
    _git(plain, "init", "-q", "-b", "main")
    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(backend, "packaged_runtime", lambda: False)

    # Only the development instance lands changes on dev.
    monkeypatch.setenv("OPENPROGRAM_PROFILE", dev_instance.DEV_PROFILE)
    assert backend.select(repo) == backend.DEV_DEPLOY
    assert backend.select(plain) == backend.CHECKOUT
    monkeypatch.setattr(backend, "packaged_runtime", lambda: True)
    assert backend.select(repo) == backend.DEV_DEPLOY
    assert backend.select(plain) == backend.LEGACY_APP

    # The official App runs the default profile: a source repository with a
    # dev branch does not make it deploy anything.
    monkeypatch.delenv("OPENPROGRAM_PROFILE")
    assert backend.select(repo) == backend.LEGACY_APP
    monkeypatch.setattr(backend, "packaged_runtime", lambda: False)
    assert backend.select(repo) == backend.CHECKOUT

    monkeypatch.setenv("OPENPROGRAM_PROFILE", dev_instance.DEV_PROFILE)
    (state / "runtime").mkdir(parents=True)
    (state / "runtime" / "developer.json").write_text("{}")
    assert backend.select(repo) == backend.CHECKOUT
    (state / "runtime" / "developer.json").unlink()
    versions = state / "runtime" / "versions" / "v1"
    versions.mkdir(parents=True)
    monkeypatch.setattr(backend.sys, "prefix", str(versions))
    assert backend.select(repo) == backend.VERSIONS
    backend.require_host(backend.VERSIONS)  # Sealed versions run on every platform.


# The whole flow

class Machine:
    """A repository, one saved request, and fakes for what would touch this computer."""

    def __init__(self, repos, tmp_path, monkeypatch):
        self.repo, self.tree, self.base = repos
        self.candidate = _commit(self.tree, "feature.txt", "feature\n")
        self.store = SelfUpdateStore(tmp_path / "self-updates")
        self.update_id = "su_devdeploy"
        request = UpdateRequest(
            update_id=self.update_id, session_id="s", origin_turn_id="u", origin_assistant_id="a", agent_id="main",
            repo=str(self.repo), worktree_id="wt", base_sha=self.base, candidate_sha=self.candidate,
            changed_paths=("feature.txt",), pre_update_evidence=("git-status:clean", dev_deploy.EVIDENCE),
            goal="Add the feature", assertions=("It works",), timeout_seconds=3600)
        self.store.create(request)
        self.store._write_json(self.store.root / self.update_id / dev_deploy.JOURNAL,
                               {"schema": 1, "worktree": str(self.tree), "python": "/python"})
        self.calls: list = []
        self.served = {"dev": None}
        self.dev_healthy = True

        monkeypatch.setattr(dev_instance, "dev_revision", lambda: self.served["dev"])
        monkeypatch.setattr(dev_instance, "healthy", lambda port: self.dev_healthy)
        monkeypatch.setattr(dev_instance, "deploy_dev", self.deploy)
        monkeypatch.setattr(system_probe, "doctor_baseline", lambda record, target, **_: {})
        monkeypatch.setattr(system_probe, "probe_instance", self.probe)
        monkeypatch.setattr(system_probe, "probe_browser", lambda target, **_: "passed")
        monkeypatch.setattr(dev_deploy, "HEALTH_WAIT_SECONDS", 0)

    def deploy(self, sha):
        self.calls.append(("deploy", sha))
        self.served["dev"] = sha
        return sha

    def probe(self, record, target, *, revision=None, **_):
        self.calls.append(("probe", target.port))
        if not self.dev_healthy:
            raise system_probe.SystemProbeError("system probe failed: health")
        sha = self.served["dev"]
        assert revision is None or revision == sha
        return {"schema": 1, "candidate_sha": sha, "attempt": record.state.attempt, "worker_pid": 1,
                "verified_at": 1.0, "checks": {}}

    def release(self):
        self.store.transition(self.update_id, UpdatePhase.BUILDING, expected_phase=UpdatePhase.PREPARING)

    def run(self):
        dev_deploy._Controller(self.store, self.update_id).advance()
        return self.store.load(self.update_id).state

    def dev_tip(self):
        return _git(self.repo, "rev-parse", "dev")


@pytest.fixture
def machine(repos, tmp_path, monkeypatch):
    return Machine(repos, tmp_path, monkeypatch)


def test_a_candidate_lands_deploys_the_development_instance_and_stops_there(machine):
    machine.release()
    state = machine.run()
    tip = machine.dev_tip()
    assert state.phase is UpdatePhase.SUCCEEDED
    assert state.detail["landed_sha"] == tip
    assert state.detail["development_system_gate"]["candidate_sha"] == tip
    # Only the development instance is deployed and observed; nothing else is touched.
    assert machine.calls == [("deploy", tip), ("probe", dev_instance.DEV_PORT)]
    assert _git(machine.repo, "branch", "--format=%(refname:short)").split() == ["dev", "op/wt/one"]


def test_nothing_happens_before_the_origin_turn_is_saved(machine, monkeypatch):
    monkeypatch.setattr(dev_deploy, "TURN_WAIT_SECONDS", 0)
    state = machine.run()
    assert state.phase is UpdatePhase.ABORTED
    assert machine.calls == [] and machine.dev_tip() == machine.base


def test_a_conflict_aborts_with_the_file_list(machine):
    _commit(machine.repo, "feature.txt", "theirs\n")
    machine.release()
    state = machine.run()
    assert state.phase is UpdatePhase.ABORTED
    assert state.detail["files"] == ["feature.txt"]
    assert machine.calls == []


def test_a_failed_deploy_aborts_and_leaves_the_commit_on_dev(machine, monkeypatch):
    def broken(sha):
        machine.calls.append(("deploy", sha))
        raise dev_instance.InstanceError("refresh-dev-instance.sh failed (exit 1)")

    monkeypatch.setattr(dev_instance, "deploy_dev", broken)
    machine.release()
    state = machine.run()
    tip = machine.dev_tip()
    assert state.phase is UpdatePhase.ABORTED
    assert f"{tip[:10]} is on dev, but the development instance did not take it" in state.detail["reason"]
    assert machine.calls == [("deploy", tip)], "no revert and no second deploy"


def test_an_instance_that_stays_unhealthy_aborts_without_a_revert(machine):
    machine.release()
    machine.dev_healthy = False
    state = machine.run()
    assert state.phase is UpdatePhase.ABORTED and "did not take it" in state.detail["reason"]
    assert _git(machine.repo, "rev-list", "--count", f"{machine.base}..dev") == "1"


def test_a_commit_the_instance_already_serves_is_not_deployed_again(machine):
    machine.release()
    landed = land(machine.repo, machine.tree)
    machine.served["dev"] = landed
    state = machine.run()
    assert state.phase is UpdatePhase.SUCCEEDED and state.detail["landed_sha"] == landed
    assert machine.calls == [("probe", dev_instance.DEV_PORT)]


def test_a_cancelled_update_is_left_alone(machine, monkeypatch):
    machine.release()

    def cancel_while_deploying(sha):
        machine.calls.append(("deploy", sha))
        machine.served["dev"] = sha
        machine.store.transition(machine.update_id, UpdatePhase.ABORTED, expected_phase=UpdatePhase.BUILDING,
                                 detail={"reason": "owner requested cancellation"})
        return sha

    monkeypatch.setattr(dev_instance, "deploy_dev", cancel_while_deploying)
    state = machine.run()
    assert state.phase is UpdatePhase.ABORTED and state.detail == {"reason": "owner requested cancellation"}


def test_a_controller_restarted_mid_deploy_finishes_it(machine):
    machine.release()
    landed = land(machine.repo, machine.tree)
    journal = machine.store._read_json(machine.store.root / machine.update_id / dev_deploy.JOURNAL)
    machine.store._write_json(machine.store.root / machine.update_id / dev_deploy.JOURNAL,
                              {**journal, "landed": landed, "onto": machine.base})
    state = machine.run()
    assert state.phase is UpdatePhase.SUCCEEDED and state.detail["landed_sha"] == landed
    assert machine.calls[0] == ("deploy", landed)


def test_a_worker_start_relaunches_a_dead_controller_but_never_raises(machine, monkeypatch):
    record = machine.store.load(machine.update_id)
    script = machine.store.root / machine.update_id / dev_deploy.SCRIPT
    script.write_text("#!/bin/sh\n")
    started = []
    monkeypatch.setattr(dev_deploy, "_start", lambda directory, update_id: started.append(update_id))
    dev_deploy.ensure_running(machine.store, record)
    assert started == [machine.update_id]

    def broken(*_):
        raise OSError("launchctl is gone")

    monkeypatch.setattr(dev_deploy, "_start", broken)
    dev_deploy.ensure_running(machine.store, record)


def test_a_request_saved_without_a_controller_is_ended_at_worker_start(machine):
    record = machine.store.load(machine.update_id)
    dev_deploy.ensure_running(machine.store, record)
    assert machine.store.load(machine.update_id).state.phase is UpdatePhase.ABORTED


def test_recovery_does_not_hold_the_worker_for_this_backend(machine, monkeypatch):
    from openprogram.operations.self_update.control import recovery

    machine.release()
    monkeypatch.setattr(recovery, "SelfUpdateStore", lambda: machine.store)
    seen = []
    monkeypatch.setattr(dev_deploy, "ensure_running", lambda store, record: seen.append(record.request.update_id))
    monkeypatch.setattr("openprogram.operations.self_update.repair.next_candidate.dispatch_pending", lambda: None)
    assert recovery.recover_pending_updates() is True
    assert seen == [machine.update_id]


def test_status_names_the_landed_commit(machine):
    from openprogram.operations.self_update.control.projection import read_status

    machine.release()
    state = machine.run()
    status = read_status(machine.store, session_id="s", update_id=machine.update_id)
    assert status["phase"] == "succeeded"
    assert status["dev_deploy"]["landed_sha"] == state.detail["landed_sha"]
    assert status["dev_deploy"]["browser_probe"] == "passed"


def test_the_runtime_is_cloned_into_the_update_and_probed(tmp_path, monkeypatch):
    from openprogram.operations.self_update.delivery import controller_bundle

    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "runtime-manifest.json").write_text('{"python": "bin/python"}')
    (runtime / "bin" / "python").write_text("#!/bin/sh\n")
    (runtime / "bin" / "python").chmod(0o755)
    probed = []
    monkeypatch.setattr(controller_bundle, "_probe_runtime", lambda root, python, module: probed.append(module))
    update_dir = tmp_path / "update"
    update_dir.mkdir()

    python = controller_bundle.clone_private_runtime(update_dir, runtime)

    assert python == (update_dir / "controller/runtime/bin/python").resolve() and probed
    # Replacing the source runtime leaves the controller's copy intact.
    (runtime / "bin" / "python").unlink()
    assert python.exists()
    assert controller_bundle.clone_private_runtime(update_dir, runtime) == python
