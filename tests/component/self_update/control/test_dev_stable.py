"""The stable/development controller, driven against a real Git repository."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.operations.self_update.control import backend, dev_stable
from openprogram.operations.self_update.control.dev_stable import LandFailed, land
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
    _git(repo, "branch", "stable", base)
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


def test_a_revert_is_one_commit_undoing_the_range(repos, tmp_path):
    repo, tree, base = repos
    _commit(tree, "a.txt", "a\n")
    _commit(tree, "b.txt", "b\n")
    landed = land(repo, tree)
    dev_stable.commit_revert(repo, tmp_path / "revert", base, landed, message="undo")
    assert not (tmp_path / "revert" / "a.txt").exists() and not (tmp_path / "revert" / "b.txt").exists()
    assert _git(tmp_path / "revert", "rev-list", "--count", f"{landed}..HEAD") == "1"


# The controller's environment

def test_the_controller_never_inherits_a_session_or_a_profile():
    environment = dev_stable.controller_environment("/repo", {
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
    _git(plain, "init", "-q")
    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(backend, "packaged_runtime", lambda: False)

    assert backend.select(repo) == backend.DEV_STABLE
    assert backend.select(plain) == backend.CHECKOUT
    monkeypatch.setattr(backend, "packaged_runtime", lambda: True)
    assert backend.select(plain) == backend.LEGACY_APP
    assert backend.select(repo) == backend.DEV_STABLE

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
        self.update_id = "su_devstable"
        request = UpdateRequest(
            update_id=self.update_id, session_id="s", origin_turn_id="u", origin_assistant_id="a", agent_id="main",
            repo=str(self.repo), worktree_id="wt", base_sha=self.base, candidate_sha=self.candidate,
            changed_paths=("feature.txt",), pre_update_evidence=("git-status:clean", dev_stable.EVIDENCE),
            goal="Add the feature", assertions=("It works",), timeout_seconds=7200)
        self.store.create(request)
        self.store._write_json(self.store.root / self.update_id / dev_stable.JOURNAL,
                               {"schema": 1, "worktree": str(self.tree), "python": "/python"})
        self.calls: list = []
        self.served = {"dev": None, "stable": self.base}
        self.fail_refresh = False
        self.stable_healthy = True
        self.stable_refusal = None

        monkeypatch.setattr(dev_instance, "dev_revision", lambda: self.served["dev"])
        monkeypatch.setattr(dev_instance, "healthy", lambda port: True)
        monkeypatch.setattr(dev_instance, "stable_revision", lambda: self.served["stable"])
        monkeypatch.setattr(dev_instance, "deploy_dev", self.deploy)
        monkeypatch.setattr(dev_instance, "promote", self.promote)
        monkeypatch.setattr(system_probe, "doctor_baseline", lambda record, target, **_: {})
        monkeypatch.setattr(system_probe, "probe_instance", self.probe)
        monkeypatch.setattr(system_probe, "probe_browser", lambda target, **_: "passed")
        monkeypatch.setattr(dev_stable, "HEALTH_WAIT_SECONDS", 0)
        monkeypatch.setattr("openprogram.operations.self_update.control.supervisor._wait_for_quiescence",
                            lambda deadline: self.calls.append("idle") or True)
        self.original_promote = None

    def deploy(self, sha):
        self.calls.append(("deploy", sha))
        self.served["dev"] = sha
        return sha

    def promote(self, sha, *, wait_for_stable=True):
        assert wait_for_stable is False, "the controller has already waited"
        self.calls.append(("promote", sha))
        if self.stable_refusal is not None:
            refusal, self.stable_refusal = self.stable_refusal, None
            self.served["dev"] = "somebody-elses-deploy"
            raise refusal
        if self.served["dev"] != sha:
            raise dev_instance.NotServedError("only the commit the development instance serves can be promoted")
        if self.fail_refresh:
            # A failed refresh leaves stable and the App where they were.
            self.fail_refresh = False
            raise dev_instance.RefreshFailedError(f"{sha[:10]} was not promoted: refresh-local-app.sh failed")
        _git(self.repo, "branch", "-f", "stable", sha)
        self.served["stable"] = sha
        return sha

    def probe(self, record, target, *, revision=None, **_):
        if target.port == dev_instance.STABLE_PORT and not self.stable_healthy:
            raise system_probe.SystemProbeError("system probe failed: health")
        if target.port == dev_instance.DEV_PORT:
            sha = self.served["dev"]
        else:
            sha = self.served["stable"]
        assert revision is None or revision == sha
        return {"schema": 1, "candidate_sha": sha, "attempt": record.state.attempt, "worker_pid": 1,
                "verified_at": 1.0, "checks": {}}

    def release(self):
        self.store.transition(self.update_id, UpdatePhase.BUILDING, expected_phase=UpdatePhase.PREPARING)

    def run(self):
        dev_stable._Controller(self.store, self.update_id).advance()
        return self.store.load(self.update_id).state

    def promotions(self):
        return [call[1] for call in self.calls if isinstance(call, tuple) and call[0] == "promote"]

    def dev_tip(self):
        return _git(self.repo, "rev-parse", "dev")


@pytest.fixture
def machine(repos, tmp_path, monkeypatch):
    return Machine(repos, tmp_path, monkeypatch)


def test_a_candidate_lands_deploys_waits_for_idle_and_promotes(machine):
    machine.release()
    state = machine.run()
    tip = machine.dev_tip()
    assert state.phase is UpdatePhase.SUCCEEDED
    assert state.detail["landed_sha"] == tip
    assert machine.calls == [("deploy", tip), "idle", ("promote", tip)]
    assert _git(machine.repo, "rev-parse", "stable") == tip
    assert "backend" not in state.detail


def test_nothing_happens_before_the_origin_turn_is_saved(machine, monkeypatch):
    monkeypatch.setattr(dev_stable, "TURN_WAIT_SECONDS", 0)
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


def test_another_deploy_that_took_dev_over_is_redeployed_once(machine):
    machine.release()
    machine.stable_refusal = dev_instance.NotServedError("only the commit the development instance serves")
    state = machine.run()
    tip = machine.dev_tip()
    assert state.phase is UpdatePhase.SUCCEEDED
    assert machine.calls.count(("deploy", tip)) == 2
    assert machine.promotions() == [tip, tip]


def test_a_second_refusal_aborts_and_stable_is_untouched(machine, monkeypatch):
    def refuse(sha, **_):
        machine.calls.append(("promote", sha))
        raise dev_instance.NotServedError("only the commit the development instance serves")

    monkeypatch.setattr(dev_instance, "promote", refuse)
    machine.release()
    state = machine.run()
    assert state.phase is UpdatePhase.ABORTED
    assert _git(machine.repo, "rev-parse", "stable") == machine.base
    assert len(machine.promotions()) == 2


def test_a_commit_stable_already_contains_is_not_promoted_again(machine):
    machine.release()
    # Another session's promotion of a later commit carried this one to stable.
    landed = land(machine.repo, machine.tree)
    later = _commit(machine.repo, "later.txt", "later\n")
    _git(machine.repo, "branch", "-f", "stable", later)
    machine.served["stable"] = later
    state = machine.run()
    assert state.phase is UpdatePhase.SUCCEEDED and state.detail["landed_sha"] == landed
    assert machine.promotions() == []


def test_a_failed_refresh_is_repaired_forward_with_a_revert(machine):
    machine.release()
    machine.fail_refresh = True
    state = machine.run()
    landed, revert = state.detail["landed_sha"], state.detail["revert_sha"]
    assert state.phase is UpdatePhase.ROLLED_BACK
    assert revert == machine.dev_tip() != landed
    assert revert[:10] in state.detail["reason"]
    assert not (machine.repo / "feature.txt").exists()
    assert machine.promotions() == [landed, revert]
    # stable only ever moved forward.
    assert _git(machine.repo, "rev-parse", "stable") == revert
    assert _git(machine.repo, "merge-base", "--is-ancestor", landed, revert) == ""


def test_a_stable_instance_that_stays_unhealthy_is_repaired_forward(machine, monkeypatch):
    machine.release()
    calls = {"n": 0}
    original = machine.probe

    def probe(record, target, *, revision=None, **kw):
        if target.port == dev_instance.STABLE_PORT:
            calls["n"] += 1
            if calls["n"] == 1:
                raise system_probe.SystemProbeError("system probe failed: health")
        return original(record, target, revision=revision, **kw)

    monkeypatch.setattr(system_probe, "probe_instance", probe)
    state = machine.run()
    assert state.phase is UpdatePhase.ROLLED_BACK
    assert "did not come up healthy" in state.detail["reason"]
    assert _git(machine.repo, "rev-parse", "stable") == state.detail["revert_sha"]


def test_a_repair_that_also_fails_asks_for_the_owner(machine):
    machine.release()
    machine.stable_healthy = False
    state = machine.run()
    assert state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY
    assert "the repair also failed" in state.detail["reason"]


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
    assert machine.promotions() == []


def test_a_controller_restarted_mid_promotion_finishes_it(machine):
    machine.release()
    landed = land(machine.repo, machine.tree, note=lambda onto, head: None)
    journal = machine.store._read_json(machine.store.root / machine.update_id / dev_stable.JOURNAL)
    machine.store._write_json(machine.store.root / machine.update_id / dev_stable.JOURNAL,
                              {**journal, "landed": landed, "onto": machine.base})
    machine.store.transition(machine.update_id, UpdatePhase.READY, expected_phase=UpdatePhase.BUILDING,
                             detail={"landed_sha": landed})
    machine.store.transition(machine.update_id, UpdatePhase.ACTIVATING, expected_phase=UpdatePhase.READY,
                             detail={"landed_sha": landed})
    machine.served["dev"] = landed
    # The previous controller moved stable, then died before the App refresh ended.
    _git(machine.repo, "branch", "-f", "stable", landed)
    state = machine.run()
    assert state.phase is UpdatePhase.SUCCEEDED
    assert machine.promotions() == [landed]


def test_a_worker_start_relaunches_a_dead_controller_but_never_raises(machine, monkeypatch):
    record = machine.store.load(machine.update_id)
    script = machine.store.root / machine.update_id / dev_stable.SCRIPT
    script.write_text("#!/bin/sh\n")
    started = []
    monkeypatch.setattr(dev_stable, "_start", lambda directory, update_id: started.append(update_id))
    dev_stable.ensure_running(machine.store, record)
    assert started == [machine.update_id]

    def broken(*_):
        raise OSError("launchctl is gone")

    monkeypatch.setattr(dev_stable, "_start", broken)
    dev_stable.ensure_running(machine.store, record)


def test_a_request_saved_without_a_controller_is_ended_at_worker_start(machine):
    record = machine.store.load(machine.update_id)
    dev_stable.ensure_running(machine.store, record)
    assert machine.store.load(machine.update_id).state.phase is UpdatePhase.ABORTED


def test_recovery_does_not_hold_the_worker_for_this_backend(machine, monkeypatch):
    from openprogram.operations.self_update.control import recovery

    machine.release()
    monkeypatch.setattr(recovery, "SelfUpdateStore", lambda: machine.store)
    seen = []
    monkeypatch.setattr(dev_stable, "ensure_running", lambda store, record: seen.append(record.request.update_id))
    monkeypatch.setattr("openprogram.operations.self_update.repair.next_candidate.dispatch_pending", lambda: None)
    assert recovery.recover_pending_updates() is True
    assert seen == [machine.update_id]


def test_status_names_the_landed_commit_and_the_reason(machine):
    from openprogram.operations.self_update.control.projection import read_status

    machine.release()
    machine.fail_refresh = True
    state = machine.run()
    status = read_status(machine.store, session_id="s", update_id=machine.update_id)
    assert status["phase"] == "rolled_back"
    assert status["dev_stable"]["revert_sha"] == state.detail["revert_sha"]
    assert "reverted on dev" in status["dev_stable"]["reason"]


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


def test_a_later_promotion_still_installing_is_waited_for(machine, monkeypatch):
    machine.release()
    landed = land(machine.repo, machine.tree)
    later = _commit(machine.repo, "later.txt", "later\n")
    _git(machine.repo, "branch", "-f", "stable", later)  # The other session's refresh is still running.
    probes = {"n": 0}
    real = machine.probe

    def installing(record, target, *, revision=None, **kw):
        if target.port == dev_instance.STABLE_PORT:
            probes["n"] += 1
            if probes["n"] == 3:
                machine.served["stable"] = later
        return real(record, target, revision=revision, **kw)

    monkeypatch.setattr(system_probe, "probe_instance", installing)
    monkeypatch.setattr(dev_stable.time, "sleep", lambda _seconds: None)
    state = machine.run()
    assert state.phase is UpdatePhase.SUCCEEDED and state.detail["landed_sha"] == landed
    assert machine.promotions() == []


def test_a_commit_a_later_promotion_carries_is_never_reverted(machine, monkeypatch):
    machine.release()
    land(machine.repo, machine.tree)
    later = _commit(machine.repo, "later.txt", "later\n")
    _git(machine.repo, "branch", "-f", "stable", later)
    monkeypatch.setattr(dev_stable, "PROMOTED_REFRESH_SECONDS", 0)
    tip = machine.dev_tip()
    state = machine.run()
    assert state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY and "later promotion" in state.detail["reason"]
    assert machine.dev_tip() == tip, "no revert was made"


def _stable_answers(machine, monkeypatch, answers):
    """The stable port gives each of ``answers`` in turn, then what it really serves."""
    remaining = iter(answers)
    real = machine.probe

    def probe(record, target, *, revision=None, **kw):
        if target.port == dev_instance.STABLE_PORT:
            answer = next(remaining, None)
            if isinstance(answer, Exception):
                raise answer
            if answer is not None:
                return {**real(record, target, revision=None, **kw), "candidate_sha": answer}
        return real(record, target, revision=revision, **kw)

    monkeypatch.setattr(system_probe, "probe_instance", probe)
    monkeypatch.setattr(dev_stable.time, "sleep", lambda _seconds: None)


def test_a_worker_down_for_another_sessions_whole_refresh_is_waited_for(machine, monkeypatch):
    machine.release()
    landed = land(machine.repo, machine.tree)
    later = _commit(machine.repo, "later.txt", "later\n")
    _git(machine.repo, "branch", "-f", "stable", later)
    machine.served["stable"] = later
    down = system_probe.SystemProbeError("system probe failed: health")
    _stable_answers(machine, monkeypatch, [down, down, down])
    state = machine.run()
    assert state.phase is UpdatePhase.SUCCEEDED and state.detail["landed_sha"] == landed


def test_the_old_worker_answering_while_exactly_this_commit_installs_reverts_nothing(machine, monkeypatch):
    machine.release()
    landed = land(machine.repo, machine.tree)
    # Another session promoted this very commit: the App's files are in place and its worker has not restarted.
    _git(machine.repo, "branch", "-f", "stable", landed)
    machine.served["stable"] = landed
    monkeypatch.setattr(dev_stable, "HEALTH_WAIT_SECONDS", 60)
    _stable_answers(machine, monkeypatch, [machine.base, machine.base])
    tip = machine.dev_tip()
    state = machine.run()
    assert state.phase is UpdatePhase.SUCCEEDED
    assert machine.dev_tip() == tip, "no revert was made"
    assert machine.promotions() == []


def test_a_failed_refresh_never_reverts_a_commit_a_later_promotion_carries(machine, monkeypatch):
    machine.release()
    land(machine.repo, machine.tree)
    later = _commit(machine.repo, "later.txt", "later\n")

    def overtaken(sha, **_):
        machine.calls.append(("promote", sha))
        _git(machine.repo, "branch", "-f", "stable", later)
        raise dev_instance.RefreshFailedError(f"{sha[:10]} was not promoted: refresh-local-app.sh failed")

    monkeypatch.setattr(dev_instance, "promote", overtaken)
    tip = machine.dev_tip()
    state = machine.run()
    assert state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY and "later promotion" in state.detail["reason"]
    assert machine.dev_tip() == tip, "no revert was made"


def test_a_promotion_refused_because_a_later_one_overtook_it_is_verified(machine, monkeypatch):
    machine.release()
    landed = land(machine.repo, machine.tree)
    later = _commit(machine.repo, "later.txt", "later\n")

    def refused(sha, **_):
        machine.calls.append(("promote", sha))
        _git(machine.repo, "branch", "-f", "stable", later)
        machine.served["stable"] = later
        raise dev_instance.InstanceError(f"{sha[:10]} does not contain stable; promotion only fast-forwards")

    monkeypatch.setattr(dev_instance, "promote", refused)
    state = machine.run()
    assert state.phase is UpdatePhase.SUCCEEDED and state.detail["landed_sha"] == landed
