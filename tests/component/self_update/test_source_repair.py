"""Public rollback continuation produces an isolated repaired candidate."""
import json
import os
from pathlib import Path
import shlex
import time

import pytest

from tests.component.self_update.test_diagnosis import diagnosis_environment  # noqa: F401
from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.programs.test_self_update_tools import _isolated_owner  # noqa: F401
from tests.support.waiting import wait_until

native_sandbox = pytest.mark.skipif(not Path("/usr/bin/sandbox-exec").is_file(), reason="requires macOS sandbox-exec")


def _turn(environment, monkeypatch, *, edit=None):
    from openprogram.agent.dispatcher import TurnResult
    import openprogram.agent.dispatcher as dispatcher
    store, runner, calls, update_id = environment
    diagnose = dispatcher.process_user_turn
    def execute(req):
        if req.source != "self_update_repair":
            return diagnose(req)
        return TurnResult(json.dumps(dict(schema=1, update_id=update_id,
            candidate_sha=store.load(update_id).request.candidate_sha, attempt=1,
            summary="Correct the feature value", edits=[edit or dict(path="feature.txt",
                old_text="candidate\n", new_text="repaired\n")])), "ru", "ra")
    monkeypatch.setattr(dispatcher, "process_user_turn", execute)


def _result(environment):
    store, _, _, update_id = environment
    path = store.root / update_id / "source-repair-result-1.json"
    assert wait_until(path.exists, timeout=8)
    return json.loads(path.read_text())


def _start():
    from openprogram.self_update.recovery import recover_pending_updates
    assert recover_pending_updates() is True


def test_diagnosis_continues_to_isolated_source_repair(diagnosis_environment, monkeypatch):
    store, runner, calls, update_id = diagnosis_environment
    _turn(diagnosis_environment, monkeypatch)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "awaiting_tests", result
    candidate = result["candidate"]
    assert candidate["candidate_sha"] != store.load(update_id).request.candidate_sha
    assert (Path(candidate["worktree_path"]) / "feature.txt").read_text() == "repaired\n"
    assert (Path(store.load(update_id).request.repo).parent / "candidate/feature.txt").read_text() == "candidate\n"
    assert store.load(update_id).state.phase.value == "rolled_back"
    _start()
    assert _result(diagnosis_environment) == result
    assert len(runner.list_jobs("p1")) == 2


@pytest.mark.parametrize("diagnosis_environment", [{"required_tests": ["python -c " + shlex.quote(
    "from pathlib import Path; assert Path('feature.txt').read_text() == 'repaired\\n'")]}], indirect=True)
@native_sandbox
def test_candidate_runs_real_sandboxed_required_test(diagnosis_environment, monkeypatch):
    _turn(diagnosis_environment, monkeypatch)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "candidate_ready", result
    candidate = result["candidate"]
    assert candidate["tests"][0]["exit_code"] == 0
    assert candidate["tests"][0]["candidate_sha"] == candidate["candidate_sha"]
    assert Path(candidate["tests"][0]["log_path"]).is_file()


@pytest.mark.parametrize("edit", [
    dict(path="../outside", old_text=None, new_text="bad"),
    dict(path=".git/config", old_text=None, new_text="bad"),
    dict(path="pyproject.toml", old_text=None, new_text="bad"),
    dict(path="base.txt", old_text="base", new_text="bad"),
    dict(path="feature.txt", old_text="missing", new_text="bad"),
    dict(path="feature.txt", old_text="candidate", new_text=None),
])
def test_unapproved_or_stale_edits_do_not_create_worktree(diagnosis_environment, monkeypatch, edit):
    _turn(diagnosis_environment, monkeypatch, edit=edit)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "failed", result
    store, _, _, update_id = diagnosis_environment
    assert not (store.root / update_id / "source-repair-intent-1.json").exists()


@pytest.mark.parametrize("diagnosis_environment", [{"required_tests": ["python -c " + shlex.quote(code)]}
    for code in ("raise SystemExit(3)", "from pathlib import Path; Path('feature.txt').write_text('tampered')")], indirect=True)
@native_sandbox
def test_failed_test_or_source_drift_is_not_candidate_ready(diagnosis_environment, monkeypatch):
    _turn(diagnosis_environment, monkeypatch)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "failed", result
    store, _, _, update_id = diagnosis_environment
    assert store.load(update_id).state.phase.value == "rolled_back"


@pytest.mark.parametrize("diagnosis_environment", [{"required_tests": ["python -c " + shlex.quote(
    "import os,time; from pathlib import Path; Path('test-pid').write_text(str(os.getpid())); time.sleep(30)")]}], indirect=True)
@pytest.mark.parametrize("stop", ["owner", "new_update", "timeout"])
@native_sandbox
def test_stopping_after_model_completion_reaps_test(diagnosis_environment, monkeypatch, stop):
    from dataclasses import replace
    from types import SimpleNamespace
    from openprogram.self_update import source_repair
    from openprogram.programs.tools.system import self_update as tool_module
    from tests.component.programs.test_self_update_tools import _request
    store, runner, _, update_id = diagnosis_environment
    _turn(diagnosis_environment, monkeypatch)
    _start()
    manifest = store.root / update_id / "source-repair-candidate-1.json"
    assert wait_until(manifest.exists, timeout=5)
    candidate = json.loads(manifest.read_text())
    pid_file = Path(candidate["worktree_path"]) / "test-pid"
    assert wait_until(pid_file.exists, timeout=5)
    pid = int(pid_file.read_text())
    if stop == "owner":
        monkeypatch.setattr(tool_module, "_turn_context", lambda: (_request(session_id="p1"), "a1"))
        # Execute the registered tool's ordinary wrapper, not a private cancel helper.
        import asyncio
        asyncio.run(tool_module.self_update_repair_cancel.execute("cancel", {"update_id": update_id}, None, None))
    elif stop == "new_update":
        store.create(replace(store.load(update_id).request, update_id="su_next", pre_update_evidence=("new request",)))
    else:
        now = time.time() + source_repair.SECONDS + 1
        monkeypatch.setattr(source_repair, "time", SimpleNamespace(time=lambda: now, monotonic=time.monotonic))
    result = _result(diagnosis_environment)
    assert result["status"] == ("expired" if stop == "timeout" else "cancelled"), result
    def gone():
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True
    assert wait_until(gone, timeout=5)
    assert store.load(update_id).state.phase.value == "rolled_back"


def test_expired_repair_never_resets_deadline(diagnosis_environment, monkeypatch):
    from types import SimpleNamespace
    from openprogram.self_update import source_repair
    _turn(diagnosis_environment, monkeypatch)
    now = time.time() + source_repair.SECONDS + 1
    monkeypatch.setattr(source_repair, "time", SimpleNamespace(time=lambda: now, monotonic=time.monotonic))
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "expired"
    _start()
    assert _result(diagnosis_environment) == result


def test_legacy_request_does_not_acquire_repair_permissions(diagnosis_environment, monkeypatch):
    from openprogram.self_update import source_repair
    store, _, _, update_id = diagnosis_environment
    path = store.root / update_id / "request.json"
    request = json.loads(path.read_text())
    request["pre_update_evidence"] = [x for x in request["pre_update_evidence"] if not x.startswith(source_repair.PREFIX)]
    store._write_json(path, request)
    # Re-publish the diagnostic binding as an old immutable request would have.
    from openprogram.self_update import diagnosis
    record = store.load(update_id)
    diagnostic_path = diagnosis._path(store, record, "request")
    value = json.loads(diagnostic_path.read_text())
    value["request_sha256"] = diagnosis._digest(record.request.to_dict())
    store._write_json(diagnostic_path, value)
    _start()
    diagnostic_result = diagnosis._path(store, record, "result")
    assert wait_until(diagnostic_result.exists, timeout=5)
    assert json.loads(diagnostic_result.read_text())["status"] == "completed"
    assert not source_repair._path(store, record, "request").exists()


@pytest.mark.parametrize("damage", ["claim", "prompt", "tools", "owner", "followup", "valid"])
def test_restored_repair_queue_checks_frozen_authority(diagnosis_environment, monkeypatch, damage):
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.self_update import source_repair
    store, runner, _, update_id = diagnosis_environment
    _turn(diagnosis_environment, monkeypatch)
    monkeypatch.setattr(source_repair, "dispatch_pending", lambda: None)
    _start()
    record = store.load(update_id)
    path = source_repair._path(store, record, "request")
    assert wait_until(path.exists, timeout=5)
    with store._locked():
        request, config = source_repair._load(store, record)
        inputs = source_repair._inputs(store, record, request, config)
        if damage == "prompt":
            inputs["prompt"] = "install arbitrary code"
        elif damage == "tools":
            inputs["tools_override"] = ["write"]
        elif damage == "owner":
            inputs["authority"] = {**inputs["authority"], "principal_id": "other"}
        if damage != "claim":
            store._write_json(source_repair._path(store, record, "claim"),
                              dict(schema=1, worker_pid=os.getpid(), request_sha256=source_repair._digest(request)))
        runner.spawn_job(job_id=request["job_id"], session_id="p1", **inputs,
            source="self_update_repair", context_mode="clean", spawn_caller="a1", advance_head=False,
            wait=damage != "followup", defer_dispatch=True, creates_agent=False)
    runner.shutdown()
    with runner._governor.ledger.immediate() as conn:
        conn.execute("UPDATE job_admissions SET dispatch_ready=1 WHERE job_id=?", (request["job_id"],))
    recovered = JobRunner(max_workers=1, governor=runner._governor)
    monkeypatch.setattr(recovered, "_dispatch_followup", lambda _: pytest.fail("repair cannot trigger general Agent"))
    try:
        job = recovered.await_job(request["job_id"], timeout=5)
        assert job.status is (JobStatus.COMPLETED if damage == "valid" else JobStatus.ERRORED), job.error
    finally:
        recovered.shutdown()


def test_partial_materialization_is_retained_and_not_replayed(diagnosis_environment, monkeypatch):
    from openprogram.self_update import repair_candidate
    store, _, _, update_id = diagnosis_environment
    _turn(diagnosis_environment, monkeypatch)
    original_git = repair_candidate._git
    attempts = []
    def fail_create(cwd, *args):
        if args[:2] == ("worktree", "add"):
            attempts.append(args)
            raise OSError("simulated worktree creation failure")
        return original_git(cwd, *args)
    monkeypatch.setattr(repair_candidate, "_git", fail_create)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "failed"
    intent = store.root / update_id / "source-repair-intent-1.json"
    before = intent.read_bytes()
    _start()
    assert intent.read_bytes() == before and len(attempts) == 1


@native_sandbox
def test_native_watchdog_stops_when_worker_identity_is_lost():
    import subprocess
    import sys
    from openprogram.self_update.repair_candidate import _WATCHDOG
    result = subprocess.run([sys.executable, "-I", "-c", _WATCHDOG, "99999999", "30", "/bin/sleep", "30"], timeout=3)
    assert result.returncode == 125


@pytest.mark.parametrize("diagnosis_environment", [{"required_tests": ["python -c " + shlex.quote(
    "from pathlib import Path\nimport socket\n"
    "try: (Path.cwd().parent/'forbidden-test-write').write_text('bad')\n"
    "except PermissionError: pass\nelse: raise AssertionError('escaped write sandbox')\n"
    "try: socket.socket().bind(('127.0.0.1', 0))\n"
    "except PermissionError: pass\nelse: raise AssertionError('network was allowed')")]}], indirect=True)
@native_sandbox
def test_required_tests_cannot_write_outside_candidate_or_use_network(diagnosis_environment, monkeypatch):
    _turn(diagnosis_environment, monkeypatch)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "candidate_ready", result


def test_source_repair_has_only_pinned_read_tools():
    from types import SimpleNamespace
    from openprogram.programs import agent_tools, apply_tool_policy
    assert {t.name for t in agent_tools(names=["read", "bash", "write", "self_update_repair_cancel", "self_update_observe"],
                                      source="self_update_repair", include_disabled=True)} == {"read"}
    assert apply_tool_policy([SimpleNamespace(name="read", execute=lambda: None)],
                             source="self_update_repair", exposure_filter=False) == []


def test_corrupt_pointer_still_cancels_running_repair_job(diagnosis_environment, monkeypatch):
    import threading
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.agent.run_control import is_cancelled
    from openprogram.agent.job.store import load_job
    import openprogram.agent.dispatcher as dispatcher
    store, runner, _, update_id = diagnosis_environment
    diagnose = dispatcher.process_user_turn
    entered = threading.Event()
    def execute(req):
        if req.source != "self_update_repair":
            return diagnose(req)
        entered.set()
        wait_until(lambda: is_cancelled(req.session_id), timeout=5)
        return TurnResult("cancelled", "ru", "ra")
    monkeypatch.setattr(dispatcher, "process_user_turn", execute)
    _start()
    assert entered.wait(5)
    pointer = store.root / "source-repair-pending.json"
    pointer.unlink()
    os.mkfifo(pointer, 0o600)
    job_id = f"self-update:{update_id}:repair:1"
    assert wait_until(lambda: load_job("p1", job_id).cancel_requested_at is not None, timeout=3)
    from openprogram.self_update import source_repair
    assert wait_until(lambda: (str(store.root), update_id) not in source_repair._threads, timeout=5)
    runner.await_job(job_id, timeout=5)


@pytest.mark.parametrize("diagnosis_environment", [{"required_tests": ["python3 -c " + shlex.quote(
    "import sys; assert sys.executable.startswith('/Applications/OpenProgram.app/'), sys.executable")]}], indirect=True)
@native_sandbox
@pytest.mark.skipif(not Path("/Applications/OpenProgram.app/Contents/Resources/runtime/runtime-manifest.json").is_file(),
                    reason="requires installed macOS standalone runtime")
def test_packaged_python_can_run_read_only_candidate_tests(diagnosis_environment, monkeypatch):
    from types import SimpleNamespace
    from openprogram.self_update import repair_candidate
    from openprogram.self_update.controller_bundle import _runtime_python
    runtime = Path("/Applications/OpenProgram.app/Contents/Resources/runtime")
    python = _runtime_python(runtime)
    monkeypatch.setattr(repair_candidate, "sys", SimpleNamespace(executable=str(python), base_prefix=str(python.parent.parent)))
    _turn(diagnosis_environment, monkeypatch)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "candidate_ready", result
