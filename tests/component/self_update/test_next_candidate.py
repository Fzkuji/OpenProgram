"""A repaired candidate continues under the original bounded update authority."""
import shlex
import time
import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from tests.component.self_update.test_diagnosis import diagnosis_environment  # noqa: F401
from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.programs.test_self_update_tools import _isolated_owner, _candidate, _Manager, _request  # noqa: F401
from tests.component.self_update.test_source_repair import _turn, _start, _result, native_sandbox
from tests.support.waiting import wait_until
from tests.component.self_update.test_install_transaction import installation, INSTALLER, version, phase  # noqa: F401
from tests.component.config.test_distribution_release import MACOS_DESKTOP_INSTALL
from tests.component.self_update.test_package_protocol import package_factory  # noqa: F401
from tests.component.self_update.test_verification_plan import _plan


@pytest.mark.parametrize("diagnosis_environment", [{
    "verification_plan": _plan(), "mode": "bounded_auto", "max_attempts": 3,
    "deadline": time.time() + 3600, "allowed_paths": ["feature.txt"],
    "required_tests": ["python -c 'assert True'"],
}], indirect=True)
@native_sandbox
def test_planned_repair_preserves_contract_in_jobs_and_child(diagnosis_environment, monkeypatch):
    from openprogram.agent.job.store import load_job
    from openprogram.self_update.verifier_config import load_verifier_config, verifier_prompt
    from openprogram.self_update.next_candidate import chain
    store, _, _, update_id = diagnosis_environment
    monkeypatch.setattr(
        "openprogram.self_update.launcher.launch_supervisor",
        lambda *_args, **_kwargs: None,
    )
    original = store.load(update_id)
    _turn(diagnosis_environment, monkeypatch)
    _start()
    assert _result(diagnosis_environment)["status"] == "candidate_ready"
    assert wait_until(lambda: store.load_active() is not None, timeout=5)
    child = store.load_active()
    config = load_verifier_config(store, child)
    assert config["verification_plan"] == _plan()
    assert [item.request.update_id for item in chain(store, child)] == [update_id, child.request.update_id]
    for phase in ("diagnose", "repair"):
        job = load_job(original.request.session_id, f"self-update:{update_id}:{phase}:1")
        contract = json.loads(job.prompt.split("\n", 1)[1])
        assert contract["verification_plan"] == _plan()
        assert contract["iteration_policy"] == original.request.iteration_policy.to_dict()
        assert contract["timeout_seconds"] == original.request.timeout_seconds
        assert job.tools_override == ["read", "glob", "grep", "list"]
    contract = json.loads(verifier_prompt(child, config).split("\n", 1)[1])
    assert contract["verification_plan"] == _plan()
    assert contract["iteration_policy"] == original.request.iteration_policy.to_dict()
    assert contract["timeout_seconds"] == child.request.timeout_seconds <= original.request.timeout_seconds
    assert contract["attempt"] == 2


def test_iteration_approval_never_hides_envelope_after_a_long_goal():
    from openprogram.agent.internals._approval import _approval_detail
    args = dict(goal="goal " * 1000, iteration_policy=dict(mode="bounded_auto", max_attempts=3,
                deadline=12345, allowed_paths=["feature.txt"], required_tests=["python -m pytest"]))
    for name in ("self_update_prepare", "self_update_retry"):
        detail = _approval_detail(name, args)
        assert "bounded_auto" in detail and "max_attempts" in detail and "12345" in detail
        assert "已截断" not in detail


@pytest.mark.parametrize("diagnosis_environment", [{
    "mode": "bounded_auto", "max_attempts": 3, "deadline": time.time() + 3600,
    "allowed_paths": ["feature.txt"],
    "required_tests": ["python -c " + shlex.quote("assert True")],
}], indirect=True)
@native_sandbox
def test_bounded_repair_submits_new_child_with_original_budget(diagnosis_environment, monkeypatch):
    store, _, _, update_id = diagnosis_environment
    original = store.load(update_id)
    launches = []
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor",
                        lambda next_id, **kwargs: launches.append(next_id))
    _turn(diagnosis_environment, monkeypatch)
    _start()
    repaired = _result(diagnosis_environment)
    assert repaired["status"] == "candidate_ready", repaired

    assert wait_until(lambda: store.load_active() is not None, timeout=5), (
        "validated bounded repair did not create its next update request"
    )
    child = store.load_active()
    assert child.request.update_id != update_id
    assert child.state.attempt == 2
    assert child.request.candidate_sha == repaired["candidate"]["candidate_sha"]
    assert child.request.base_sha == original.request.base_sha
    assert child.request.goal == original.request.goal
    assert child.request.assertions == original.request.assertions
    assert child.request.iteration_policy == original.request.iteration_policy
    assert store.load(update_id) == original
    assert wait_until(lambda: child.request.update_id in launches, timeout=5)
    from openprogram.self_update.projection import read_status
    journal = store.root / update_id / "events.jsonl"
    journal.write_bytes(b"".join(journal.read_bytes().splitlines(keepends=True)[:-1]))
    before = journal.read_bytes(), journal.stat().st_mtime_ns
    projected = read_status(store, session_id=child.request.session_id, update_id=child.request.update_id)
    parent = read_status(store, session_id=original.request.session_id, update_id=update_id)
    assert projected["root_id"] == update_id and projected["parent_id"] == update_id
    assert parent["source_repair_result"]["candidate_sha"] == child.request.candidate_sha
    assert (journal.read_bytes(), journal.stat().st_mtime_ns) == before


@pytest.mark.parametrize("diagnosis_environment", [{"required_tests": ["python -c 'assert True'"]}], indirect=True)
@pytest.mark.parametrize("decision", ["allow", "deny", "dirty", "log", "log_link_race"])
@native_sandbox
def test_default_retry_requires_fresh_exact_one_shot_approval(diagnosis_environment, monkeypatch, decision):
    from openprogram.agent.internals import _approval
    from openprogram.programs.tools.system import self_update as tools
    from openprogram.self_update.handoff import release_prepared_update
    from tests.component.programs.test_self_update_tools import _request
    store, _, _, update_id = diagnosis_environment
    _turn(diagnosis_environment, monkeypatch)
    _start()
    repaired = _result(diagnosis_environment)
    assert repaired["status"] == "candidate_ready"
    assert store.load_active() is None
    req = _request(session_id="p1", user_msg_id="retry_user", permission_mode="bypass")
    monkeypatch.setattr(tools, "_turn_context", lambda: (req, "retry_reply"))
    launched = []
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda uid, **kw: launched.append(uid))
    persisted = []
    monkeypatch.setattr(_approval, "_persist_always_allow_rule", lambda *a: persisted.append(a))
    approvals = []
    async def approve(**kwargs):
        approvals.append(kwargs["args"])
        if decision == "dirty":
            (Path(repaired["candidate"]["worktree_path"]) / "feature.txt").write_text("changed after approval display")
        if decision == "log":
            Path(repaired["candidate"]["tests"][0]["log_path"]).write_text("changed after approval display")
        if decision == "log_link_race":
            log = Path(repaired["candidate"]["tests"][0]["log_path"])
            replacement = log.with_name("replacement.log")
            replacement.write_bytes(log.read_bytes())
            resolve = Path.resolve
            swapped = False
            def swap_after_resolve(path, *args, **kwargs):
                nonlocal swapped
                result = resolve(path, *args, **kwargs)
                if path == log and not swapped:
                    swapped = True
                    log.unlink()
                    log.symlink_to(replacement)
                return result
            monkeypatch.setattr(Path, "resolve", swap_after_resolve)
        return decision != "deny", None, "always"
    monkeypatch.setattr(_approval, "await_user_approval", approve)
    wrapped = _approval.wrap_with_approval(tools.self_update_retry, req, lambda _: None)
    args = dict(update_id=update_id, candidate_sha=repaired["candidate"]["candidate_sha"])
    outcome = asyncio.run(wrapped.execute("retry", args, None, None))
    assert len(approvals) == 1 and not persisted
    assert approvals[0]["candidate"]["candidate_sha"] == args["candidate_sha"]
    assert approvals[0]["candidate"]["changed_paths"] == ["feature.txt"]
    if decision == "allow":
        assert not outcome.is_error, outcome
        child = store.load_active()
        assert child.state.phase.value == "preparing" and child.request.update_id in launched
        assert release_prepared_update("p1", "a1", store=store) is None
        assert release_prepared_update("p1", "retry_reply", store=store).phase.value == "staging"
    else:
        assert outcome.is_error, outcome
        assert store.load_active() is None and not launched


@pytest.mark.parametrize("diagnosis_environment", [{"required_tests": ["python -c 'assert True'"]}], indirect=True)
@native_sandbox
def test_whole_iteration_cancel_stops_waiting_candidate(diagnosis_environment, monkeypatch):
    from openprogram.programs.tools.system import self_update as tools
    from openprogram.self_update import next_candidate
    from tests.component.programs.test_self_update_tools import _request
    store, _, _, update_id = diagnosis_environment
    _turn(diagnosis_environment, monkeypatch)
    _start()
    repaired = _result(diagnosis_environment)
    req = _request(session_id="p1")
    monkeypatch.setattr(tools, "_turn_context", lambda: (req, "stop_reply"))
    result = asyncio.run(tools.self_update_iteration_cancel.execute("stop", {"update_id": update_id}, None, None))
    assert not result.is_error, result
    _start()
    with pytest.raises(ValueError, match="cancelled"):
        next_candidate.approval_preview(update_id, repaired["candidate"]["candidate_sha"], req)
    assert store.load_active() is None


@pytest.mark.parametrize("diagnosis_environment", [
    {"required_tests": ["python -c 'assert True'"]},
    {"mode": "bounded_auto", "max_attempts": 3, "deadline": time.time() + 3600,
     "allowed_paths": ["feature.txt"], "required_tests": ["python -c 'assert True'"]},
], indirect=True)
@pytest.mark.parametrize("cut", ["reservation", "active"])
@native_sandbox
def test_startup_resumes_one_reserved_child_without_refreshing_budget(diagnosis_environment, monkeypatch, cut):
    from openprogram.self_update import next_candidate, source_repair, SelfUpdateStore
    from tests.component.programs.test_self_update_tools import _request
    store, _, _, update_id = diagnosis_environment
    dispatch = next_candidate.dispatch_pending
    monkeypatch.setattr(next_candidate, "dispatch_pending", lambda: None)
    _turn(diagnosis_environment, monkeypatch)
    _start()
    repaired = _result(diagnosis_environment)
    assert wait_until(lambda: (str(store.root), update_id) not in source_repair._threads, timeout=5)
    write = SelfUpdateStore._write_json
    class LostWorker(BaseException):
        pass
    def lose(self, path, value):
        write(self, path, value)
        if path.name == ("iteration-next.json" if cut == "reservation" else "active.json"):
            raise LostWorker()
    monkeypatch.setattr(SelfUpdateStore, "_write_json", lose)
    automatic = store.load(update_id).request.iteration_policy.mode.value == "bounded_auto"
    with pytest.raises(LostWorker):
        next_candidate.submit(update_id, repaired["candidate"]["candidate_sha"],
            **({} if automatic else dict(req=_request(session_id="p1", user_msg_id="retry_user"),
                                       assistant_id="retry_reply")))
    reservation_path = store.root / update_id / "iteration-next.json"
    reservation_bytes = reservation_path.read_bytes()
    reservation = json.loads(reservation_bytes)
    monkeypatch.setattr(SelfUpdateStore, "_write_json", write)
    launched = []
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda uid, **kw: launched.append(uid))
    monkeypatch.setattr(next_candidate, "dispatch_pending", dispatch)
    _start()
    child = store.load_active()
    assert child.request.update_id == reservation["request"]["update_id"]
    assert child.state.attempt == 2
    assert child.request.created_at == reservation["request"]["created_at"]
    assert child.request.iteration_policy.deadline == store.load(update_id).request.iteration_policy.deadline
    assert reservation_path.read_bytes() == reservation_bytes
    assert child.request.update_id in launched
    _start()
    assert store.load_active().request == child.request
    assert reservation_path.read_bytes() == reservation_bytes
    if not automatic:
        from openprogram.self_update.handoff import release_prepared_update
        assert store.load_active().state.phase.value == "preparing"
        assert release_prepared_update("p1", "another_reply", store=store) is None
        assert release_prepared_update("p1", "retry_reply", store=store).phase.value == "staging"


@pytest.mark.parametrize("diagnosis_environment", [{
    "mode": "bounded_auto", "max_attempts": 3, "deadline": time.time() + 3600,
    "allowed_paths": ["feature.txt"], "required_tests": ["python -c 'assert True'"],
}], indirect=True)
@native_sandbox
def test_expired_pending_candidate_cannot_refresh_original_deadline(diagnosis_environment, monkeypatch):
    from types import SimpleNamespace
    from openprogram.self_update import next_candidate, source_repair
    store, _, _, update_id = diagnosis_environment
    dispatch = next_candidate.dispatch_pending
    monkeypatch.setattr(next_candidate, "dispatch_pending", lambda: None)
    _turn(diagnosis_environment, monkeypatch)
    _start()
    assert _result(diagnosis_environment)["status"] == "candidate_ready"
    assert wait_until(lambda: (str(store.root), update_id) not in source_repair._threads, timeout=5)
    original_request = (store.root / update_id / "request.json").read_bytes()
    deadline = store.load(update_id).request.iteration_policy.deadline
    launches = []
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda uid, **kw: launches.append(uid))
    monkeypatch.setattr(next_candidate, "time", SimpleNamespace(time=lambda: deadline + 1))
    monkeypatch.setattr(next_candidate, "dispatch_pending", dispatch)
    _start()
    _start()
    assert store.load_active() is None and not launches
    assert not (store.root / update_id / "iteration-next.json").exists()
    assert "deadline exhausted" in next_candidate.status(store, store.load(update_id))["reason"]
    assert (store.root / update_id / "request.json").read_bytes() == original_request


@pytest.mark.parametrize("diagnosis_environment", [
    {"required_tests": ["python -c 'assert True'"]},
    {"mode": "bounded_auto", "max_attempts": 3, "deadline": time.time() + 3600,
     "allowed_paths": ["feature.txt"], "required_tests": ["python -c 'assert True'"]},
], indirect=True)
@native_sandbox
def test_launch_failure_aborts_published_child_before_startup(diagnosis_environment, monkeypatch):
    from openprogram.self_update import next_candidate, source_repair
    from tests.component.programs.test_self_update_tools import _request
    store, _, _, update_id = diagnosis_environment
    dispatch = next_candidate.dispatch_pending
    monkeypatch.setattr(next_candidate, "dispatch_pending", lambda: None)
    _turn(diagnosis_environment, monkeypatch)
    _start()
    repaired = _result(diagnosis_environment)
    assert wait_until(lambda: (str(store.root), update_id) not in source_repair._threads, timeout=5)
    launches = []
    def fail(child_id, **kwargs):
        launches.append(child_id)
        raise RuntimeError("supervisor launch failed")
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", fail)
    automatic = store.load(update_id).request.iteration_policy.mode.value == "bounded_auto"
    with pytest.raises(RuntimeError, match="supervisor launch failed"):
        next_candidate.submit(update_id, repaired["candidate"]["candidate_sha"],
                              **({} if automatic else dict(req=_request(session_id="p1", user_msg_id="retry_user"),
                                                          assistant_id="retry_reply")))
    reservation = (store.root / update_id / "iteration-next.json").read_bytes()
    child_id = json.loads(reservation)["request"]["update_id"]
    assert store.load(child_id).state.phase.value == "aborted"
    assert store.load_active() is None
    assert next_candidate.status(store, store.load(update_id))["status"] == "stopped"
    monkeypatch.setattr(next_candidate, "dispatch_pending", dispatch)
    _start()
    assert launches == [child_id]
    assert (store.root / update_id / "iteration-next.json").read_bytes() == reservation


@pytest.mark.parametrize("diagnosis_environment", [{
    "mode": "bounded_auto", "max_attempts": 3, "deadline": time.time() + 3600,
    "allowed_paths": ["feature.txt"], "required_tests": ["python -c 'assert True'"],
}], indirect=True)
@native_sandbox
def test_same_failure_on_child_stops_before_another_repair_job(diagnosis_environment, monkeypatch):
    import os
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.self_update import UpdatePhase, source_repair
    from openprogram.self_update.maintenance import leave_maintenance
    from openprogram.self_update.recovery import SYSTEM_CHECKS
    from openprogram.self_update.rollback_intent import begin_rollback
    store, runner, _, parent_id = diagnosis_environment
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda *a, **kw: None)
    _turn(diagnosis_environment, monkeypatch)
    _start()
    assert _result(diagnosis_environment)["status"] == "candidate_ready"
    assert wait_until(lambda: (str(store.root), parent_id) not in source_repair._threads, timeout=5)
    child = store.load_active()
    child_id = child.request.update_id
    # Reuse the verified-rollback boundary from diagnosis_environment; no App is installed.
    for state in (UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(child_id, state)
    detail = {"previous_system_gate": {"candidate_sha": "3" * 40}, "error": "system probe failed: web"}
    store.transition(child_id, UpdatePhase.VERIFYING, detail=detail)
    begin_rollback(store, child_id, "system probe failed: web")
    gate = dict(schema=1, candidate_sha="3" * 40, attempt=2, verified_at=time.time(),
                worker_pid=os.getpid(), checks={key: True for key in SYSTEM_CHECKS})
    store.transition(child_id, UpdatePhase.ROLLED_BACK, detail={**detail, "restored_system_gate": gate})
    store._write_json(store.root / "maintenance.json", dict(schema=1, update_id=child_id, entered_at=time.time()))
    def diagnose(req):
        return TurnResult(json.dumps(dict(schema=1, update_id=child_id, candidate_sha=child.request.candidate_sha,
            attempt=2, category="implementation", cause="The same behavior still fails",
            evidence_refs=["failure"], corrections=["Correct the return value"])), "du2", "da2")
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", diagnose)
    leave_maintenance(child_id)
    _start()
    result_path = store.root / child_id / "source-repair-result-2.json"
    assert wait_until(result_path.exists, timeout=5)
    result = json.loads(result_path.read_text())
    assert not any(job.id == f"self-update:{child_id}:repair:2" for job in runner.list_jobs("p1"))
    assert result["status"] == "failed" and "repeated failure" in result["reason"], result
    assert not (store.root / child_id / "iteration-next.json").exists()
    assert store.load_active() is None


@pytest.mark.parametrize("diagnosis_environment", [{"required_tests": ["python -c 'assert True'"]}], indirect=True)
@native_sandbox
def test_other_session_cannot_approve_or_cancel_iteration(diagnosis_environment, monkeypatch):
    from openprogram.self_update import next_candidate
    from tests.component.programs.test_self_update_tools import _request
    store, _, _, update_id = diagnosis_environment
    _turn(diagnosis_environment, monkeypatch)
    _start()
    repaired = _result(diagnosis_environment)
    for action in (
        lambda: next_candidate.approval_preview(update_id, repaired["candidate"]["candidate_sha"], _request(session_id="other")),
        lambda: next_candidate.cancel(update_id, _request(session_id="other")),
    ):
        with pytest.raises(ValueError, match="another session"):
            action()
    assert store.load_active() is None


@pytest.mark.parametrize("diagnosis_environment", [{
    "mode": "bounded_auto", "max_attempts": 2, "deadline": time.time() + 3600,
    "allowed_paths": ["feature.txt"], "required_tests": ["python -c 'assert True'"],
}], indirect=True)
@pytest.mark.parametrize("verdict", ["pass", "fail"])
@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
@native_sandbox
def test_submitted_child_uses_native_transaction_and_new_verifier(
    diagnosis_environment, installation, package_factory, monkeypatch, verdict,
):
    import hashlib
    import os
    import threading
    from fastapi import FastAPI, WebSocket
    from fastapi.responses import HTMLResponse
    from openprogram.agent.authority import owner_principal_id
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.agent.turn_request_context import set_turn_request, reset_turn_request
    from openprogram.cli.commands import doctor
    from openprogram.programs import get_agent_tool
    from openprogram.self_update import supervisor, recovery, system_probe
    from openprogram.webui.routes import misc
    from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from tests.component.providers import test_web_owner_auth_listener as listener

    store, runner, _, parent_id = diagnosis_environment
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda *a, **kw: None)
    _turn(diagnosis_environment, monkeypatch)
    _start()
    assert _result(diagnosis_environment)["status"] == "candidate_ready"
    assert wait_until(lambda: store.load_active() is not None, timeout=5)
    child = store.load_active()
    update_id = child.request.update_id
    assert child.state.attempt == 2
    directory = store.root / update_id
    installer = directory / "controller/install-app.sh"
    installer.parent.mkdir()
    installer.write_bytes(INSTALLER.read_bytes())
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    _, artifact, target, native_tmp = installation
    package_factory("child-installed", app=target)
    package_factory("child-candidate", app=artifact)
    monkeypatch.setattr(supervisor, "DEFAULT_APP_PATH", str(target))
    native_run = supervisor.subprocess.run
    def run(args, **kwargs):
        if args[:2] == ["/bin/bash", str(installer)]:
            kwargs["env"] = {**kwargs["env"], "DESTDIR": str(target.parent.parent),
                "HOME": str(native_tmp / "home"), "TMPDIR": str(native_tmp / "tmp"), "PATH": os.environ["PATH"]}
        return native_run(args, **kwargs)
    monkeypatch.setattr(supervisor.subprocess, "run", run)
    def transaction(path):
        assert path.parent == target.parent and path.name.startswith(".openprogram-app-install.")
        return path
    monkeypatch.setattr(supervisor, "_validate_transaction_path", transaction)
    builds = []
    def build(record, path):
        builds.append((record.request.update_id, record.state.attempt))
        return supervisor.Artifact(artifact, supervisor._tree_digest(artifact))
    monkeypatch.setattr(supervisor, "_build_candidate", build)
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda _: True)
    port = listener._free_port()
    monkeypatch.setattr(system_probe, "_PORT", port)
    monkeypatch.setattr(misc, "_HEAD_SHA", "3" * 40)
    monkeypatch.setattr("openprogram.worker.lifecycle.current_worker_pid", os.getpid)
    monkeypatch.setattr(doctor, "run_checks", lambda: [
        dict(id=fn.__name__, ok=True, label=fn.__name__, detail="fixture") for fn in doctor.CHECKS])
    app = FastAPI()
    misc.register(app)
    @app.get("/chat")
    async def chat():
        return HTMLResponse('<script src="/_next/test.js"></script>')
    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "functions_list"}))
        assert await ws.receive_text() == "ping"
        await ws.send_text(json.dumps({"type": "pong"}))
        await ws.close()
    auth = OwnerAuthState.start(state_dir=store.root.parent, bind_host="127.0.0.1", port=port,
                               allowed_origins=(), raw_token=bytes(range(32)), owner_principal_id=owner_principal_id())
    monkeypatch.setattr(listener, "_app", lambda _: OwnerAuthMiddleware(app, auth_state=auth))
    verification_calls = []
    def execute(req):
        if req.source != "self_update_verify":
            return TurnResult("{}", "diagnostic_u", "diagnostic_a")
        verification_calls.append(req)
        token = set_turn_request(req)
        try:
            observed = asyncio.run(get_agent_tool("self_update_observe").execute(
                "observe", {"entry": "/api/diagnostics"}, None, None))
            assert not observed.is_error, observed
            evidence = json.loads(observed.content[0].text)
            return TurnResult(json.dumps(dict(schema=1, update_id=update_id,
                candidate_sha=child.request.candidate_sha, attempt=2, verdict=verdict,
                assertions=[dict(id="acceptance-1", status=verdict, entry=evidence["entry"],
                    observation="Authenticated child diagnostics", evidence_refs=[evidence["evidence_ref"]],
                    observed_at=evidence["observed_at"])])), "verify_u", "verify_a")
        finally:
            reset_turn_request(token)
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", execute)
    threads, startup = [], []
    def restart():
        thread = threading.Thread(target=lambda: startup.append(recovery.recover_pending_updates()))
        threads.append(thread)
        thread.start()
    activate = supervisor._activate
    def install(*args):
        result = activate(*args)
        assert version(target) == "0.6.2"
        monkeypatch.setattr(misc, "_HEAD_SHA", child.request.candidate_sha)
        restart()
        return result
    monkeypatch.setattr(supervisor, "_activate", install)
    command = supervisor._installer_command
    def finalize(path, directory, digest, mode, **kwargs):
        result = command(path, directory, digest, mode, **kwargs)
        if mode == "--rollback":
            assert (directory / "rollback-2.json").exists()
            assert version(target) == "0.6.1"
            monkeypatch.setattr(misc, "_HEAD_SHA", "3" * 40)
            restart()
        return result
    monkeypatch.setattr(supervisor, "_installer_command", finalize)
    try:
        with listener._listener(auth, port):
            outcome = supervisor.run_supervisor(update_id, state_root=store.root, installer_sha256=digest)
            for thread in threads:
                thread.join(timeout=5)
                assert not thread.is_alive()
            current = store.load(update_id)
            assert outcome == (0 if verdict == "pass" else 1), current.state.detail
            assert current.state.phase.value == ("succeeded" if verdict == "pass" else "rolled_back"), current.state.detail
            assert builds == [(update_id, 2)] and len(verification_calls) == 1
            intent = json.loads((directory / "reopen-2.json").read_text())
            assert intent["update_id"] == update_id and intent["attempt"] == 2
            assert intent["session_id"] == child.request.session_id
            assert runner.await_job(f"self-update:{update_id}:verify:2", timeout=5) is not None
            assert startup and all(startup)
            assert phase(Path(current.state.detail["transaction_dir"])) == ("committed" if verdict == "pass" else "rolled_back")
            assert store.load(parent_id).state.phase.value == "rolled_back"
    finally:
        for thread in threads:
            thread.join(timeout=5)
        auth.close()


@pytest.mark.parametrize("checks", [("web", "doctor", "health"), ("unknown",), ("",)])
@native_sandbox
def test_system_failure_identity_and_total_attempt_budget(tmp_path, monkeypatch, store_fixture, _isolated_owner, checks):
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.programs.tools.system.self_update import _prepare_update
    from openprogram.self_update import SelfUpdateStore, source_repair, UpdatePhase
    from openprogram.self_update.maintenance import leave_maintenance
    from openprogram.self_update.recovery import SYSTEM_CHECKS, recover_pending_updates
    from openprogram.self_update.rollback_intent import begin_rollback
    store = SelfUpdateStore()
    worktree, _, sha = _candidate(tmp_path)
    worktree = replace(worktree, parent_session="p1")
    prepared = _prepare_update(worktree_id=worktree.id, candidate_sha=sha, goal="Fix behavior",
        assertions=["Expected behavior is observable"],
        iteration_policy=dict(mode="bounded_auto", max_attempts=3, deadline=time.time() + 3600,
            allowed_paths=["feature.txt"], required_tests=["python -c 'assert True'"]),
        req=_request(session_id="p1", user_msg_id="u1"), assistant_id="a1", manager=_Manager(worktree), store=store)
    runner = JobRunner(max_workers=1)
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    monkeypatch.setattr("openprogram.agent.job.runner._broadcast", lambda *a, **k: None)
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda *a, **k: None)
    current = [store.load(prepared["update_id"])]
    root = current[0]
    child_ids = {root.request.update_id}
    def execute(req):
        record = current[0]
        identity = dict(schema=1, update_id=record.request.update_id,
                        candidate_sha=record.request.candidate_sha, attempt=record.state.attempt)
        if req.source == "self_update_diagnose":
            output = dict(**identity, category="implementation", cause="Fix the failing system endpoint",
                          evidence_refs=["failure"], corrections=["Correct the response"])
        else:
            output = dict(**identity, summary="Correct endpoint behavior", edits=[dict(path="feature.txt",
                old_text="candidate\n" if record.state.attempt == 1 else "repaired\n",
                new_text="repaired\n" if record.state.attempt == 1 else "fixed-again\n")])
        return TurnResult(json.dumps(output), "job-user", "job-assistant")
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", execute)
    try:
        for attempt, check in enumerate(checks, 1):
            error = "system probe failed: " + check if check else ""
            record = current[0]
            uid = record.request.update_id
            if record.state.phase is UpdatePhase.PREPARING:
                store.transition(uid, UpdatePhase.STAGING)
            store.transition(uid, UpdatePhase.READY)
            store.transition(uid, UpdatePhase.ACTIVATING, detail={"previous_system_gate": {"candidate_sha": "3" * 40}})
            begin_rollback(store, uid, error)
            detail = store.load(uid).state.detail
            gate = dict(schema=1, candidate_sha="3" * 40, attempt=attempt, verified_at=time.time(),
                        worker_pid=os.getpid(), checks={key: True for key in SYSTEM_CHECKS})
            store.transition(uid, UpdatePhase.ROLLED_BACK, detail={**detail, "error": error, "restored_system_gate": gate})
            store._write_json(store.root / "maintenance.json", dict(schema=1, update_id=uid, entered_at=time.time()))
            leave_maintenance(uid)
            assert recover_pending_updates() is True
            result_path = store.root / uid / f"source-repair-result-{attempt}.json"
            assert wait_until(result_path.exists, timeout=10)
            result = json.loads(result_path.read_text())
            assert wait_until(lambda: (str(store.root), uid) not in source_repair._threads, timeout=5)
            if attempt == 3:
                assert result["status"] == "failed" and "attempt budget exhausted" in result["reason"], result
                assert store.load_active() is None
                assert not any(job.id == f"self-update:{uid}:repair:3" for job in runner.list_jobs("p1"))
                assert not (store.root / uid / "iteration-next.json").exists()
                break
            assert result["status"] == "candidate_ready", result
            if check in {"unknown", ""}:
                from openprogram.self_update import next_candidate
                status = next_candidate.status(store, store.load(uid))
                assert status["status"] == "stopped" and "failure evidence" in status["reason"], status
                assert store.load_active() is None
                assert not (store.root / uid / "iteration-next.json").exists()
                break
            assert wait_until(lambda: store.load_active() is not None, timeout=5)
            current[0] = store.load_active()
            assert current[0].state.attempt == attempt + 1
            assert current[0].request.update_id not in child_ids
            child_ids.add(current[0].request.update_id)
            for field in ("goal", "assertions", "base_sha", "iteration_policy", "agent_id", "session_id"):
                assert getattr(current[0].request, field) == getattr(root.request, field)
            from openprogram.self_update.next_candidate import chain
            assert len(chain(store, current[0])) == attempt + 1
        assert len(child_ids) == (3 if len(checks) == 3 else 1)
    finally:
        runner.shutdown()


@pytest.mark.parametrize("diagnosis_environment", [{
    "mode": "bounded_auto", "max_attempts": 3, "deadline": time.time() + 3600,
    "allowed_paths": ["feature.txt"], "required_tests": ["python -c 'assert True'"],
}], indirect=True)
@pytest.mark.parametrize("case", ["concurrent", "cancel_pending", "cancel_preparing", "cancel_activating", "cancel_verifying"])
@native_sandbox
def test_submission_and_cancellation_boundaries(diagnosis_environment, monkeypatch, case):
    from concurrent.futures import ThreadPoolExecutor
    from openprogram.self_update import next_candidate, source_repair, UpdatePhase
    from openprogram.programs.tools.system import self_update as tool_module
    import asyncio
    store, runner, _, uid = diagnosis_environment
    monkeypatch.setattr(next_candidate, "dispatch_pending", lambda: None)
    launches = []
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda child, **kw: launches.append(child))
    _turn(diagnosis_environment, monkeypatch)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "candidate_ready"
    assert wait_until(lambda: (str(store.root), uid) not in source_repair._threads, timeout=5)
    sha = result["candidate"]["candidate_sha"]
    if case == "concurrent":
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: next_candidate.submit(uid, sha), range(2)))
        assert outcomes[0]["child_id"] == outcomes[1]["child_id"]
        child = store.load_active()
        assert child.state.attempt == 2
        assert {row["child_id"] for row in outcomes} == {child.request.update_id}
        assert len([path for path in store.root.iterdir() if path.name.startswith("su_")]) == 2
        return
    if case != "cancel_pending":
        outcome = next_candidate.submit(uid, sha)
        child = store.load(outcome["child_id"])
        if case in {"cancel_activating", "cancel_verifying"}:
            store.transition(child.request.update_id, UpdatePhase.READY)
            store.transition(child.request.update_id, UpdatePhase.ACTIVATING)
            if case == "cancel_verifying":
                store.transition(child.request.update_id, UpdatePhase.VERIFYING)
        original_child = store.load(child.request.update_id)
    original_root = store.load(uid)
    reservation = (store.root / uid / "iteration-next.json")
    reserved_bytes = reservation.read_bytes() if reservation.exists() else None
    monkeypatch.setattr(tool_module, "_turn_context", lambda: (_request(session_id="p1"), "cancel-reply"))
    outcome = asyncio.run(tool_module.self_update_iteration_cancel.execute("cancel", {"update_id": uid}, None, None))
    assert not outcome.is_error, outcome
    assert store.load(uid) == original_root
    if case in {"cancel_pending", "cancel_preparing"}:
        assert store.load_active() is None
    else:
        assert store.load_active() == original_child
    assert not (store.root / "iteration-pending.json").exists()
    assert reserved_bytes == (reservation.read_bytes() if reservation.exists() else None)
    with pytest.raises(ValueError, match="cancelled"):
        next_candidate.submit(uid, sha)


@pytest.mark.parametrize("stage", ["diagnose", "repair"])
def test_iteration_cancel_reaps_owned_running_job(diagnosis_environment, monkeypatch, stage):
    import asyncio
    import threading
    import openprogram.agent.dispatcher as dispatcher
    from openprogram.agent.run_control import is_cancelled
    from openprogram.programs.tools.system import self_update as tools
    from openprogram.self_update import diagnosis, source_repair
    store, runner, _, uid = diagnosis_environment
    original = store.load(uid)
    execute_diagnosis = dispatcher.process_user_turn
    entered = threading.Event()
    def execute(req):
        if req.source != f"self_update_{stage}":
            return execute_diagnosis(req)
        entered.set()
        assert wait_until(lambda: is_cancelled(req.session_id), timeout=8)
        return dispatcher.TurnResult("{}", "cancel-u", "cancel-a")
    monkeypatch.setattr(dispatcher, "process_user_turn", execute)
    _start()
    assert entered.wait(5)
    monkeypatch.setattr(tools, "_turn_context", lambda: (_request(session_id="p1"), "cancel-reply"))
    outcome = asyncio.run(tools.self_update_iteration_cancel.execute("cancel", {"update_id": uid}, None, None))
    assert not outcome.is_error, outcome
    job_id = f"self-update:{uid}:{stage}:1"
    job = runner.await_job(job_id, timeout=5)
    assert job.cancel_requested_at is not None
    assert wait_until(lambda: (str(store.root), uid) not in source_repair._threads, timeout=5)
    assert wait_until(lambda: (str(store.root), uid) not in diagnosis._monitors, timeout=5)
    assert store.load(uid) == original
    assert store.load_active() is None


@pytest.fixture
def legacy_writer(monkeypatch):
    from openprogram.self_update import SelfUpdateStore, next_candidate
    create = SelfUpdateStore.create
    def write_legacy(self, request, **kwargs):
        request = replace(request, pre_update_evidence=tuple(
            value for value in request.pre_update_evidence if not value.startswith(next_candidate.PREFIX)))
        kwargs.pop("iteration_config", None)
        return create(self, request, **kwargs)
    monkeypatch.setattr(SelfUpdateStore, "create", write_legacy)


@pytest.mark.parametrize("diagnosis_environment", [{
    "mode": "bounded_auto", "max_attempts": 3, "deadline": time.time() + 3600,
    "allowed_paths": ["feature.txt"], "required_tests": ["python -c 'assert True'"],
}], indirect=True)
@pytest.mark.usefixtures("legacy_writer")
@native_sandbox
def test_valid_legacy_candidate_waits_for_explicit_new_approval(diagnosis_environment, monkeypatch):
    from openprogram.self_update import next_candidate, source_repair
    store, _, _, update_id = diagnosis_environment
    root = store.load(update_id)
    assert root.request.iteration_policy.mode.value == "bounded_auto"
    assert not any(value.startswith(next_candidate.PREFIX) for value in root.request.pre_update_evidence)
    launches = []
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda uid, **kw: launches.append(uid))
    _turn(diagnosis_environment, monkeypatch)
    _start()
    repaired = _result(diagnosis_environment)
    assert repaired["status"] == "candidate_ready", repaired
    assert wait_until(lambda: (str(store.root), update_id) not in source_repair._threads, timeout=5)
    sha = repaired["candidate"]["candidate_sha"]
    preview = next_candidate.approval_preview(update_id, sha, _request(session_id="p1"))
    assert preview["candidate_sha"] == sha and preview["tests"]
    with pytest.raises(ValueError, match="authorization missing"):
        next_candidate.submit(update_id, sha)
    _start()
    assert store.load_active() is None and not launches
    assert next_candidate.status(store, root)["status"] == "awaiting_approval"
    assert not (store.root / update_id / "iteration-next.json").exists()


@pytest.mark.parametrize("diagnosis_environment", [
    {"required_tests": ["python -c 'assert True'"]},
    {"mode": "bounded_auto", "max_attempts": 3, "deadline": time.time() + 3600,
     "allowed_paths": ["feature.txt"], "required_tests": ["python -c 'assert True'"]},
], indirect=True)
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
@native_sandbox
def test_first_startup_resumes_durable_repair_result_without_retesting(diagnosis_environment, monkeypatch):
    from openprogram.self_update import SelfUpdateStore, source_repair, next_candidate
    from openprogram.self_update.handoff import release_prepared_update
    from openprogram.agent.internals import _approval
    from openprogram.programs.tools.system import self_update as tools
    store, runner, _, uid = diagnosis_environment
    root = store.load(uid)
    write = SelfUpdateStore._write_json
    class LostWorker(BaseException):
        pass
    def cut_after_result(self, path, value):
        write(self, path, value)
        if path.name == "source-repair-result-1.json" and value["status"] == "candidate_ready":
            raise LostWorker()
    monkeypatch.setattr(SelfUpdateStore, "_write_json", cut_after_result)
    launches = []
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda child, **kw: launches.append(child))
    _turn(diagnosis_environment, monkeypatch)
    _start()
    result = _result(diagnosis_environment)
    assert result["status"] == "candidate_ready"
    assert wait_until(lambda: (str(store.root), uid) not in source_repair._threads, timeout=5)
    assert source_repair._pointer(store) == uid and next_candidate._pointer(store) is None
    monkeypatch.setattr(SelfUpdateStore, "_write_json", write)
    jobs = {job.id for job in runner.list_jobs("p1")}
    log = Path(result["candidate"]["tests"][0]["log_path"])
    log_before = (log.read_bytes(), log.stat().st_mtime_ns)
    result_path = store.root / uid / "source-repair-result-1.json"
    result_before = result_path.read_bytes()

    _start()  # Exactly one recovery startup must perform the continuation.
    if root.request.iteration_policy.mode.value == "approve_each_activation":
        assert store.load_active() is None and not launches
        assert next_candidate.status(store, root)["status"] == "awaiting_approval"
        req = _request(session_id="p1", user_msg_id="retry_user", permission_mode="bypass")
        monkeypatch.setattr(tools, "_turn_context", lambda: (req, "retry_reply"))
        approvals = []
        async def approve(**kwargs):
            approvals.append(kwargs["args"])
            return True, None, "once"
        monkeypatch.setattr(_approval, "await_user_approval", approve)
        wrapped = _approval.wrap_with_approval(tools.self_update_retry, req, lambda _: None)
        outcome = asyncio.run(wrapped.execute("retry", dict(update_id=uid,
            candidate_sha=result["candidate"]["candidate_sha"]), None, None))
        assert not outcome.is_error and len(approvals) == 1, outcome
        assert store.load_active().state.phase.value == "preparing"
        assert release_prepared_update("p1", "another_reply", store=store) is None
        assert release_prepared_update("p1", "retry_reply", store=store).phase.value == "staging"
    child = store.load_active()
    assert child is not None, next_candidate.status(store, root)
    assert child.state.attempt == 2 and child.request.candidate_sha == result["candidate"]["candidate_sha"]
    assert child.request.iteration_policy.deadline == root.request.iteration_policy.deadline
    assert set(launches) == {child.request.update_id}
    reservation = (store.root / uid / "iteration-next.json").read_bytes()
    _start()
    assert store.load_active().request == child.request
    assert (store.root / uid / "iteration-next.json").read_bytes() == reservation
    assert {job.id for job in runner.list_jobs("p1")} == jobs
    assert result_path.read_bytes() == result_before
    assert (log.read_bytes(), log.stat().st_mtime_ns) == log_before
