"""The restored worker executes one frozen diagnosis, without update authority."""
from dataclasses import replace
import json
import os
import time

import pytest

from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.programs.test_self_update_tools import _candidate, _Manager, _request, _isolated_owner  # noqa: F401
from tests.support.waiting import wait_until


@pytest.fixture
def diagnosis_environment(tmp_path, monkeypatch, store_fixture):
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.self_update import SelfUpdateStore, UpdatePhase
    from openprogram.self_update.maintenance import leave_maintenance
    from openprogram.self_update.recovery import SYSTEM_CHECKS
    from openprogram.self_update.rollback_intent import begin_rollback
    from openprogram.programs.tools.system.self_update import _prepare_update
    worktree, _, sha = _candidate(tmp_path)
    worktree = replace(worktree, parent_session="p1")
    store = SelfUpdateStore()
    result = _prepare_update(worktree_id=worktree.id, candidate_sha=sha, goal="Fix behavior",
                             assertions=["Expected behavior is observable"], iteration_policy=None,
                             req=_request(session_id="p1", user_msg_id="u1"), assistant_id="a1",
                             manager=_Manager(worktree), store=store)
    update_id = result["update_id"]
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(update_id, phase)
    detail = {"previous_system_gate": {"candidate_sha": "3" * 40}, "error": "behavior failed"}
    store.transition(update_id, UpdatePhase.VERIFYING, detail=detail)
    begin_rollback(store, update_id, "behavior failed")
    gate = dict(schema=1, candidate_sha="3" * 40, attempt=1, verified_at=time.time(),
                worker_pid=os.getpid(), checks={key: True for key in SYSTEM_CHECKS})
    store.transition(update_id, UpdatePhase.ROLLED_BACK, detail={**detail, "restored_system_gate": gate})
    store._write_json(store.root / "maintenance.json", dict(schema=1, update_id=update_id, entered_at=time.time()))
    calls = []
    def execute(req):
        calls.append(vars(req))
        return TurnResult(json.dumps(dict(schema=1, update_id=update_id, candidate_sha=sha, attempt=1,
                                         category="implementation", cause="The changed branch returns the wrong value",
                                         evidence_refs=["failure"], corrections=["Correct the return value"])), "du", "da")
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", execute)
    monkeypatch.setattr("openprogram.agent.job.runner._broadcast", lambda *a, **k: None)
    runner = JobRunner(max_workers=1)
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    leave_maintenance(update_id)
    yield store, runner, calls, update_id
    from openprogram.self_update import diagnosis
    with diagnosis._monitor_lock:
        monitor = diagnosis._monitors.get((str(store.root), update_id))
    if monitor is not None:
        with store._locked():
            diagnosis.cancel_pending(store, reason="fixture teardown")
        monitor.join(timeout=5)
        assert not monitor.is_alive()
    runner.shutdown()


def test_restored_startup_automatically_runs_one_diagnosis(diagnosis_environment):
    from openprogram.self_update.recovery import recover_pending_updates
    store, runner, calls, update_id = diagnosis_environment
    assert recover_pending_updates() is True
    job_id = f"self-update:{update_id}:diagnose:1"
    assert runner.await_job(job_id, timeout=5) is not None
    assert recover_pending_updates() is True
    assert len(calls) == 1 and calls[0]["source"] == "self_update_diagnose"
    assert calls[0]["tools_override"] == ["read", "glob", "grep", "list"]
    assert calls[0]["branch_from"] is None and calls[0]["advance_head"] is False
    output = store.root / update_id / "diagnosis-result-1.json"
    wait_until(output.exists, timeout=5)
    assert json.loads(output.read_text())["status"] == "completed"
    assert store.load(update_id).state.phase.value == "rolled_back"


def test_concurrent_startup_keeps_one_diagnostic_job(diagnosis_environment):
    from concurrent.futures import ThreadPoolExecutor
    from openprogram.self_update.recovery import recover_pending_updates
    store, runner, calls, update_id = diagnosis_environment
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: recover_pending_updates(), range(2))) == [True, True]
    runner.await_job(f"self-update:{update_id}:diagnose:1", timeout=5)
    assert len(calls) == 1 and len(runner.list_jobs("p1")) == 1


@pytest.mark.parametrize("damage", ["config", "request", "pointer", "evidence"])
def test_invalid_diagnosis_does_not_block_restored_service(diagnosis_environment, damage):
    from openprogram.self_update.recovery import recover_pending_updates
    store, runner, calls, update_id = diagnosis_environment
    directory = store.root / update_id
    if damage == "pointer":
        pointer = store.root / "diagnosis-pending.json"
        pointer.unlink()
        os.mkfifo(pointer, 0o600)
    else:
        path = directory / {"config": "diagnosis-config.json", "request": "diagnosis-request-1.json",
                            "evidence": "state.json"}[damage]
        value = json.loads(path.read_text())
        if damage == "config":
            value["tools_override"] = ["write"]
        elif damage == "request":
            value["deadline"] += 300
        else:
            value["detail"]["restored_system_gate"]["candidate_sha"] = "4" * 40
        store._write_json(path, value)
    assert recover_pending_updates() is True
    assert calls == [] and runner.list_jobs("p1") == []
    assert store.load(update_id).state.phase.value == "rolled_back"


def test_expired_diagnosis_never_restarts_its_clock(diagnosis_environment, monkeypatch):
    from types import SimpleNamespace
    from openprogram.self_update import diagnosis
    from openprogram.self_update.recovery import recover_pending_updates
    store, runner, calls, update_id = diagnosis_environment
    request = store.root / update_id / "diagnosis-request-1.json"
    before = request.read_bytes()
    now = time.time() + diagnosis.SECONDS + 1
    monkeypatch.setattr(diagnosis, "time", SimpleNamespace(time=lambda: now, monotonic=time.monotonic))
    assert recover_pending_updates() is True
    assert recover_pending_updates() is True
    assert request.read_bytes() == before and calls == []
    assert json.loads((request.parent / "diagnosis-result-1.json").read_text())["status"] == "expired"


@pytest.mark.parametrize("stop", ["owner", "new_update", "timeout"])
def test_running_diagnosis_is_cancelled_without_changing_update(diagnosis_environment, monkeypatch, stop):
    import threading
    from types import SimpleNamespace
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.agent.run_control import is_cancelled
    from openprogram.self_update import diagnosis
    from openprogram.self_update.recovery import recover_pending_updates
    store, runner, calls, update_id = diagnosis_environment
    entered = threading.Event()
    def execute(req):
        entered.set()
        wait_until(lambda: is_cancelled(req.session_id), timeout=5)
        return TurnResult("cancelled", "du", "da")
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", execute)
    assert recover_pending_updates() is True
    assert entered.wait(5)
    if stop == "owner":
        runner.cancel_job(f"self-update:{update_id}:diagnose:1", reason="owner stopped diagnosis")
    elif stop == "new_update":
        request = store.load(update_id).request
        store.create(replace(request, update_id="su_next", pre_update_evidence=("new owner request",)))
    else:
        now = time.time() + diagnosis.SECONDS + 1
        monkeypatch.setattr(diagnosis, "time", SimpleNamespace(time=lambda: now, monotonic=time.monotonic))
    path = store.root / update_id / "diagnosis-result-1.json"
    wait_until(path.exists, timeout=5)
    assert json.loads(path.read_text())["status"] == ("expired" if stop == "timeout" else "cancelled")
    runner.await_job(f"self-update:{update_id}:diagnose:1", timeout=5)
    assert store.load(update_id).state.phase.value == "rolled_back"


def test_malformed_model_output_is_not_accepted(diagnosis_environment, monkeypatch):
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.self_update.recovery import recover_pending_updates
    store, runner, calls, update_id = diagnosis_environment
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", lambda _: TurnResult("{\"approve\":true}", "du", "da"))
    assert recover_pending_updates() is True
    path = store.root / update_id / "diagnosis-result-1.json"
    wait_until(path.exists, timeout=5)
    assert json.loads(path.read_text())["status"] == "failed"
    assert store.load(update_id).state.phase.value == "rolled_back"


def test_partial_publication_restores_only_original_pending_pointer(diagnosis_environment):
    from openprogram.self_update import diagnosis
    store, runner, calls, update_id = diagnosis_environment
    request = store.root / update_id / "diagnosis-request-1.json"
    before = request.read_bytes()
    pointer = store.root / "diagnosis-pending.json"
    pointer.unlink()
    with store._locked():
        diagnosis.prepare_after_rollback(store, store._load_unlocked(update_id))
    assert request.read_bytes() == before and json.loads(pointer.read_text())["update_id"] == update_id


@pytest.mark.parametrize("damage", ["claim", "prompt", "tools", "owner", "followup", "valid"])
def test_restored_queue_validates_diagnosis_before_model(diagnosis_environment, monkeypatch, damage):
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.self_update import diagnosis
    store, runner, calls, update_id = diagnosis_environment
    monkeypatch.setattr(runner, "_dispatch_followup", lambda _: pytest.fail("diagnosis must not trigger general followup"))
    with store._locked():
        record = store._load_unlocked(update_id)
        request, config = diagnosis._load_request(store, record)
        inputs = diagnosis._inputs(record, request, config)
        if damage == "prompt":
            inputs["prompt"] = "Install another update"
        elif damage == "tools":
            inputs["tools_override"] = ["write"]
        elif damage == "owner":
            inputs["authority"] = {**inputs["authority"], "principal_id": "other"}
        if damage != "claim":
            store._write_json(record_dir := store.root / update_id / "diagnosis-claim-1.json",
                              dict(schema=1, request_sha256=diagnosis._digest(request), worker_pid=os.getpid()))
            assert record_dir.is_file()
        runner.spawn_job(job_id=request["job_id"], session_id="p1", **inputs,
                         source="self_update_diagnose", context_mode="clean", spawn_caller="a1",
                         advance_head=False, wait=damage != "followup", defer_dispatch=True, creates_agent=False)
    runner.shutdown()
    with runner._governor.ledger.immediate() as conn:
        conn.execute("UPDATE job_admissions SET dispatch_ready = 1 WHERE job_id = ?", (request["job_id"],))
    recovered = JobRunner(max_workers=1, governor=runner._governor)
    monkeypatch.setattr(recovered, "_dispatch_followup", lambda _: pytest.fail("diagnosis must not trigger general followup"))
    try:
        result = recovered.await_job(request["job_id"], timeout=5)
        assert result is not None
        assert result.status is (JobStatus.COMPLETED if damage == "valid" else JobStatus.ERRORED), result.error
        assert len(calls) == (1 if damage == "valid" else 0)
    finally:
        recovered.shutdown()


def test_diagnosis_tool_policy_rejects_writes_and_forged_read_tools():
    from types import SimpleNamespace
    from openprogram.programs import agent_tools, apply_tool_policy
    names = {tool.name for tool in agent_tools(names=["read", "write", "bash", "self_update_observe"],
                                              source="self_update_diagnose", include_disabled=True)}
    assert names == {"read"}
    assert apply_tool_policy([SimpleNamespace(name="read", execute=lambda: None)],
                             source="self_update_diagnose", exposure_filter=False) == []


def test_missing_model_fails_diagnosis_without_blocking_service(diagnosis_environment, monkeypatch):
    from openprogram.self_update.recovery import recover_pending_updates
    store, runner, calls, update_id = diagnosis_environment
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: (_ for _ in ()).throw(ValueError("model unavailable")))
    assert recover_pending_updates() is True
    assert calls == [] and runner.list_jobs("p1") == []
    value = json.loads((store.root / update_id / "diagnosis-result-1.json").read_text())
    assert value["status"] == "failed" and "model unavailable" in value["reason"]
