from __future__ import annotations

from dataclasses import replace
import os
import time
from types import SimpleNamespace

import pytest

from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from openprogram.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.self_update.recovery import recover_pending_updates, SYSTEM_CHECKS
from openprogram.self_update.verifier_config import freeze_verifier_config, config_evidence


@pytest.fixture
def environment(tmp_path, monkeypatch, store_fixture):
    from openprogram.agent import authority
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.dispatcher import TurnResult
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "profile")
    # These tests exercise verifier admission; launcher integration is covered
    # separately by test_startup_redispatch, without this launch boundary stub.
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda *_a, **_k: None)
    authority._reset_owner_cache_for_tests()
    monkeypatch.setattr("openprogram.agent.internals._model_tools.load_agent_profile", lambda _: {"id": "main", "system_prompt": "frozen"})
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model", lambda *a: SimpleNamespace(provider="fake", id="fixed"))
    monkeypatch.setattr("openprogram.agent.job.runner._broadcast", lambda *a, **k: None)
    calls = []
    def execute(req):
        calls.append(vars(req))
        return TurnResult("inconclusive", "verify_u", "verify_a")
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", execute)
    runner = JobRunner(max_workers=1)
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    request = UpdateRequest(
        update_id="su_recover", session_id="p1", origin_turn_id="u1", origin_assistant_id="a1",
        agent_id="main", repo=str(tmp_path), worktree_id="wt", base_sha="1" * 40, candidate_sha="2" * 40,
        changed_paths=("feature.py",), pre_update_evidence=("test:pass",), goal="Fix behavior",
        assertions=("Expected behavior is observable",),
    )
    turn = SimpleNamespace(agent_id="main", profile_snapshot=None, model_override=None, **authority.local_owner_authority())
    config = freeze_verifier_config(request, turn)
    request = replace(request, pre_update_evidence=(*request.pre_update_evidence, config_evidence(config)))
    store = SelfUpdateStore()
    store.create(request, verifier_config=config)
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(request.update_id, phase)
    def release(**changes):
        gate = dict(schema=1, candidate_sha=request.candidate_sha, attempt=1,
                    verified_at=time.time(), worker_pid=os.getpid(), checks={key: True for key in SYSTEM_CHECKS})
        gate.update(changes)
        store.transition(request.update_id, UpdatePhase.VERIFYING, detail={
            "system_gate": gate, "previous_system_gate": {"candidate_sha": "3" * 40},
        })
    yield store, runner, calls, request, release
    runner.shutdown()
    authority._reset_owner_cache_for_tests()


def test_startup_dispatch_is_stable_and_uses_frozen_inputs(environment, monkeypatch):
    store, runner, calls, request, release = environment
    release()
    monkeypatch.setattr("openprogram.agent.internals._model_tools.load_agent_profile", lambda _: pytest.fail("mutable profile reloaded"))
    assert recover_pending_updates() is True
    job_id = f"self-update:{request.update_id}:verify:1"
    assert runner.await_job(job_id, timeout=5) is not None
    assert recover_pending_updates() is True
    assert len(calls) == 1
    assert calls[0]["source"] == "self_update_verify"
    assert calls[0]["model_override"] == "fake/fixed"
    assert calls[0]["profile_snapshot"]["system_prompt"] == "frozen"
    assert calls[0]["branch_from"] is None and calls[0]["advance_head"] is False
    assert calls[0]["spawn_caller"] == "a1"
    assert store.load(request.update_id).state.dispatch.job_id == job_id


@pytest.mark.parametrize("gate_changes", [
    {"checks": {}}, {"candidate_sha": "3" * 40}, {"attempt": 2},
    {"verified_at": 0}, {"worker_pid": -1},
])
def test_failed_or_stale_system_gate_never_dispatches(environment, gate_changes):
    store, runner, calls, request, release = environment
    release(**gate_changes)
    assert recover_pending_updates() is False
    assert calls == [] and runner.list_jobs("p1") == []
    assert (store.root / request.update_id / "startup-error-1.json").is_file()


def test_missing_gate_fails_closed(environment):
    store, runner, calls, request, _ = environment
    store.transition(request.update_id, UpdatePhase.VERIFYING)
    assert recover_pending_updates() is False
    assert calls == []


def test_expired_claim_recovers_same_job_id(environment):
    store, runner, calls, request, release = environment
    release()
    claim = store.claim_verifier(request.update_id, owner="dead-worker", lease_seconds=1, now=time.time() - 10)
    assert recover_pending_updates() is True
    runner.await_job(claim.job_id, timeout=5)
    assert len(calls) == 1
    assert store.load(request.update_id).state.dispatch.generation == 2


def test_terminal_orphan_job_is_not_reexecuted(environment):
    from openprogram.agent.job.store import save_job
    from openprogram.agent.job.types import Job, JobStatus
    store, runner, calls, request, release = environment
    release()
    job_id = f"self-update:{request.update_id}:verify:1"
    # The runner's orphan reconciliation leaves a durable errored Job.
    save_job("p1", Job(id=job_id, parent_session_id="p1", prompt="verify", agent_id="main",
                      source="self_update_verify", status=JobStatus.ERRORED, error="orphaned"))
    assert recover_pending_updates() is True
    assert calls == []


@pytest.mark.parametrize("gate", ["missing", "old_worker", "rolled_back", "rolling_back", "valid"])
def test_persisted_queue_checks_gate_before_model_execution(environment, monkeypatch, gate):
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.self_update.verifier_config import load_verifier_config, verifier_prompt

    store, runner, calls, request, release = environment
    if gate == "missing":
        store.transition(request.update_id, UpdatePhase.VERIFYING)
    else:
        release(**({"worker_pid": -1} if gate == "old_worker" else {}))
    claim = store.claim_verifier(request.update_id, owner=f"worker:{os.getpid()}", lease_seconds=15)
    record = store.load(request.update_id)
    config = load_verifier_config(store, record)
    runner.spawn_job(
        job_id=claim.job_id, session_id="p1", prompt=verifier_prompt(record), agent_id="main",
        source="self_update_verify", context_mode="clean", spawn_caller="a1", advance_head=False,
        wait=True, defer_dispatch=True, creates_agent=False,
        **{key: config[key] for key in (
            "profile_snapshot", "model_override", "tools_override", "response_format", "authority",
        )},
    )
    runner.shutdown()
    # Simulate process loss after durable ready publication, before pickup.
    with runner._governor.ledger.immediate() as conn:
        conn.execute("UPDATE job_admissions SET dispatch_ready = 1 WHERE job_id = ?", (claim.job_id,))
    if gate == "rolled_back":
        store.transition(request.update_id, UpdatePhase.ROLLED_BACK)
    if gate == "rolling_back":
        from openprogram.self_update.rollback_intent import begin_rollback
        begin_rollback(store, request.update_id, "failure")
    recovered = JobRunner(max_workers=1, governor=runner._governor)
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: recovered)
    try:
        # No startup hook has run: JobRunner alone recovers persisted work.
        result = recovered.await_job(claim.job_id, timeout=5)
        assert result is not None
        if gate == "valid":
            assert result.status is JobStatus.COMPLETED, result.error
            assert len(calls) == 1
        else:
            assert result.status is JobStatus.ERRORED, result.error
            assert calls == []
        if gate != "rolling_back":
            recover_pending_updates()
        assert len(recovered.list_jobs("p1")) == 1  # Never retry a rejected Job.
    finally:
        recovered.shutdown()


def test_tampered_snapshot_fails_before_claim(environment):
    store, runner, calls, request, release = environment
    release()
    path = store.root / request.update_id / "verifier-config.json"
    path.write_text(path.read_text().replace("frozen", "tampered"))
    assert recover_pending_updates() is False
    assert store.load(request.update_id).state.dispatch is None
    assert calls == []


def test_activation_wait_does_not_dispatch_before_supervisor_release(environment, monkeypatch):
    from openprogram.self_update import recovery
    store, runner, calls, request, release = environment
    waits = []
    def pause(_seconds):
        assert calls == []
        waits.append(True)
        release()
    monkeypatch.setattr(recovery, "time", SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=pause))
    assert recover_pending_updates() is True
    assert waits == [True]
    runner.await_job(f"self-update:{request.update_id}:verify:1", timeout=5)
    assert len(calls) == 1


def test_concurrent_startup_submits_only_one_job(environment):
    from concurrent.futures import ThreadPoolExecutor
    store, runner, calls, request, release = environment
    release()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: recover_pending_updates(), range(2))) == [True, True]
    runner.await_job(f"self-update:{request.update_id}:verify:1", timeout=5)
    assert len(calls) == 1 and len(runner.list_jobs("p1")) == 1


def test_owner_change_prevents_startup(environment, monkeypatch):
    store, runner, calls, request, release = environment
    release()
    monkeypatch.setattr("openprogram.agent.authority.owner_principal_id", lambda: "different-owner")
    assert recover_pending_updates() is False
    assert calls == []


def test_model_unavailable_is_a_durable_failure(environment, monkeypatch):
    store, runner, calls, request, release = environment
    release()
    def unavailable(*_):
        raise ValueError("requested model unavailable")
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model", unavailable)
    assert recover_pending_updates() is False
    assert store.load(request.update_id).state.dispatch is None
    assert calls == []
    # A retry must not conceal the failure already reported to the supervisor.
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model", lambda *_: None)
    assert recover_pending_updates() is False
    assert calls == []


def test_activation_timeout_never_creates_a_job(environment, monkeypatch):
    from openprogram.self_update import recovery
    store, runner, calls, request, _ = environment
    clock = [0.0]
    def pause(_seconds):
        clock[0] += 100
    monkeypatch.setattr(recovery, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock[0], sleep=pause))
    assert recover_pending_updates() is False
    assert calls == []
    assert (store.root / request.update_id / "startup-error-1.json").is_file()


def test_rollback_after_claim_prevents_late_admission(environment, monkeypatch):
    store, runner, calls, request, release = environment
    release()
    original = SelfUpdateStore.claim_verifier
    def claim_then_rollback(self, *args, **kwargs):
        claim = original(self, *args, **kwargs)
        self.transition(request.update_id, UpdatePhase.ROLLED_BACK)
        return claim
    monkeypatch.setattr(SelfUpdateStore, "claim_verifier", claim_then_rollback)
    assert recover_pending_updates() is True
    assert calls == [] and runner.list_jobs("p1") == []


def test_expired_update_and_candidate_error_do_not_block_restored_startup(environment, monkeypatch):
    from openprogram.self_update import recovery
    from openprogram.self_update.rollback_intent import begin_rollback
    store, runner, calls, original, _ = environment
    store.transition(original.update_id, UpdatePhase.ROLLED_BACK)
    request = replace(original, update_id="su_expired", created_at=time.time() - 3600, timeout_seconds=1)
    store.create(request)
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING):
        store.transition(request.update_id, phase, detail={"previous_system_gate": {"candidate_sha": "3" * 40}})
    intent = begin_rollback(store, request.update_id, "update timed out")
    assert begin_rollback(store, request.update_id, "retry") == intent
    store._write_json(store.root / request.update_id / "startup-error-1.json", {"error": "candidate failed"})
    waits = []
    def pause(_):
        waits.append(True)
        assert calls == []
        store.transition(request.update_id, UpdatePhase.ROLLED_BACK)
    monkeypatch.setattr(recovery, "time", SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=pause))
    assert recover_pending_updates() is True
    assert waits == [True] and calls == [] and runner.list_jobs("p1") == []


def test_rollback_intent_after_claim_prevents_late_admission(environment, monkeypatch):
    from openprogram.self_update import recovery
    from openprogram.self_update.rollback_intent import begin_rollback
    store, runner, calls, request, release = environment
    release()
    original = SelfUpdateStore.claim_verifier
    def claim_then_restore(self, *args, **kwargs):
        claim = original(self, *args, **kwargs)
        begin_rollback(self, request.update_id, "failure")
        return claim
    monkeypatch.setattr(SelfUpdateStore, "claim_verifier", claim_then_restore)
    monkeypatch.setattr(recovery, "time", SimpleNamespace(time=time.time, monotonic=time.monotonic,
                        sleep=lambda _: store.transition(request.update_id, UpdatePhase.ROLLED_BACK)))
    assert recover_pending_updates() is True
    assert calls == [] and runner.list_jobs("p1") == []


def test_invalid_rollback_intent_cannot_dispatch(environment):
    store, runner, calls, request, release = environment
    release()
    store._write_json(store.root / request.update_id / "rollback-1.json", {"schema": 1})
    assert recover_pending_updates() is False
    assert calls == [] and runner.list_jobs("p1") == []
