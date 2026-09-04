"""Real owner-auth HTTP and WebSocket observations, without another worker."""
from dataclasses import replace
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from openprogram.self_update import SelfUpdateStore, UpdateRequest, UpdatePhase
from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.self_update.test_install_transaction import installation, INSTALLER, version, phase  # noqa: F401
from tests.component.config.test_distribution_release import MACOS_DESKTOP_INSTALL


@pytest.fixture
def live(tmp_path, monkeypatch):
    from openprogram.self_update import system_probe
    from openprogram.webui.routes import misc
    from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.cli.commands import doctor
    from tests.component.providers import test_web_owner_auth_listener as listener

    profile = tmp_path / "profile"
    port = listener._free_port()
    owner = "owner/install/0123456789abcdef"
    flags = {"doctor": True, "web": True, "ws": True, "pid": os.getpid()}
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: profile)
    monkeypatch.setattr("openprogram.agent.authority.owner_principal_id", lambda: owner)
    monkeypatch.setattr("openprogram.worker.lifecycle.current_worker_pid", lambda: flags["pid"])
    monkeypatch.setattr(system_probe, "_PORT", port)
    monkeypatch.setattr(misc, "_HEAD_SHA", "2" * 40)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: SimpleNamespace(list_sessions=lambda **_: [], count_recent_nodes=lambda _: 0))
    monkeypatch.setattr(doctor, "run_checks", lambda: [
        {"id": fn.__name__, "ok": flags["doctor"], "label": fn.__name__, "detail": "test"} for fn in doctor.CHECKS
    ])
    app = FastAPI()
    misc.register(app)
    @app.middleware("http")
    async def echoed_credential(request, call_next):
        response = await call_next(request)
        if request.url.path == "/api/diagnostics" and flags.get("echo_secret"):
            data = json.loads(b"".join([chunk async for chunk in response.body_iterator]))
            secret = request.headers["authorization"] if flags["echo_secret"] == "owner" else flags["echo_secret"]
            if flags["echo_field"] == "body":
                return JSONResponse({**data, "echoed": secret})
            return JSONResponse(data, headers={"content-type": "application/json; echoed=" + secret})
        return response
    @app.get("/chat")
    async def chat():
        return HTMLResponse(('<script src="/_next/test.js"></script>' + flags.get("padding", "")) if flags["web"] else "unavailable", status_code=200 if flags["web"] else 503)
    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "functions_list"}))
        assert await ws.receive_text() == "ping"
        await ws.send_text(json.dumps({"type": "pong" if flags["ws"] else "failure"}))
        await ws.close()
    state = OwnerAuthState.start(state_dir=profile, bind_host="127.0.0.1", port=port, allowed_origins=(), raw_token=bytes(range(32)), owner_principal_id=owner)
    authenticated_app = OwnerAuthMiddleware(app, auth_state=state)
    async def controlled_redirect(scope, receive, send):
        if scope["type"] == "http":
            flags.setdefault("requests", []).append(scope["path"])
            if scope["path"] == flags.get("redirect_entry"):
                return await RedirectResponse("/redirect-target", status_code=302)(scope, receive, send)
        return await authenticated_app(scope, receive, send)
    monkeypatch.setattr(listener, "_app", lambda _: controlled_redirect)
    store = SelfUpdateStore()
    request = UpdateRequest(update_id="su_probe", session_id="s", origin_turn_id="u", origin_assistant_id="a", agent_id="main",
                            repo=str(tmp_path), worktree_id="wt", base_sha="1" * 40, candidate_sha="2" * 40,
                            changed_paths=("feature.py",), pre_update_evidence=("tests:pass",), goal="Fix feature", assertions=("Feature works",))
    store.create(request)
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(request.update_id, phase)
    try:
        with listener._listener(state, port):
            yield store.load(request.update_id), flags, state
    finally:
        state.close()


def test_real_probes_produce_a_gate_accepted_by_startup(live):
    from openprogram.self_update.system_probe import probe_system
    from openprogram.self_update.recovery import _check_gate
    record, _, state = live
    gate = probe_system(record)
    _check_gate(replace(record, state=replace(record.state, detail={"system_gate": gate})))
    assert gate["worker_pid"] == os.getpid()
    assert state.token not in json.dumps(gate)


@pytest.mark.parametrize("failed", ["doctor", "web", "ws"])
def test_failed_observation_never_produces_a_pass(live, failed):
    from openprogram.self_update.system_probe import probe_system, SystemProbeError
    record, flags, state = live
    flags[failed] = False
    with pytest.raises(SystemProbeError) as error:
        probe_system(record)
    assert state.token not in str(error.value)


def test_wrong_candidate_is_rejected_by_real_hmac_challenge(live):
    from openprogram.self_update.system_probe import probe_system, SystemProbeError
    record, _, _ = live
    with pytest.raises(SystemProbeError, match="owner_auth"):
        probe_system(replace(record, request=replace(record.request, candidate_sha="3" * 40)))


@pytest.mark.parametrize("revision", ["3" * 40, "3" * 40 + "-dirty"])
def test_current_and_restored_probes_use_observed_revision_even_after_timeout(live, monkeypatch, revision):
    from openprogram.self_update.system_probe import probe_current_system, probe_restored_system, probe_system, SystemProbeError
    from openprogram.webui.routes import misc
    record, _, _ = live
    monkeypatch.setattr(misc, "_HEAD_SHA", revision)
    assert probe_current_system(record)["candidate_sha"] == revision != record.request.base_sha
    expired = replace(record, request=replace(record.request, created_at=0, timeout_seconds=1))
    assert probe_restored_system(expired, revision)["candidate_sha"] == revision
    with pytest.raises(SystemProbeError):
        probe_system(expired)
    with pytest.raises(SystemProbeError):
        probe_restored_system(record, "4" * 40)


def test_old_worker_with_unknown_revision_cannot_pass_preflight(live, monkeypatch):
    from openprogram.self_update.system_probe import probe_current_system, SystemProbeError
    from openprogram.webui.routes import misc
    record, _, _ = live
    monkeypatch.setattr(misc, "_HEAD_SHA", "unknown")
    with pytest.raises(SystemProbeError, match="identity"):
        probe_current_system(record)


def test_instance_switch_during_doctor_is_rejected(live, monkeypatch):
    from openprogram.self_update.system_probe import probe_system, SystemProbeError
    from openprogram.cli.commands import doctor
    record, flags, _ = live
    original = doctor.run_checks
    def switch():
        flags["pid"] = -1
        return original()
    monkeypatch.setattr(doctor, "run_checks", switch)
    with pytest.raises(SystemProbeError, match="identity"):
        probe_system(record)


@pytest.mark.parametrize("empty", [True, False])
def test_empty_or_incomplete_doctor_is_not_success(live, monkeypatch, empty):
    from openprogram.self_update.system_probe import probe_system, SystemProbeError
    from openprogram.cli.commands import doctor
    record, _, _ = live
    rows = doctor.run_checks()
    monkeypatch.setattr(doctor, "run_checks", lambda: [] if empty else rows[:-1])
    with pytest.raises(SystemProbeError, match="doctor"):
        probe_system(record)


@pytest.mark.parametrize("scenario", ["success", "rollback", "wrong_restored", "goal_pass", "goal_fail", "forged_evidence",
                                      "resume_ready", "resume_activating", "resume_verifying", "resume_committed"])
@pytest.mark.parametrize("native_install", [False, pytest.param(True, marks=[pytest.mark.macos, MACOS_DESKTOP_INSTALL])])
def test_supervisor_real_gate_controls_startup_job(store_fixture, live, monkeypatch, scenario, native_install, request):
    """Actual controller -> HTTP/WS -> durable receipt -> startup -> real Job."""
    import hashlib
    from openprogram.agent import authority
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.self_update import supervisor, recovery
    from openprogram.self_update.verifier_config import freeze_verifier_config, config_evidence
    from openprogram.webui.routes import misc

    # This fixture drives the controller directly; do not also launch a native
    # service. Startup-to-launcher integration has its own public-entry tests.
    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda *_a, **_k: None)
    fixtures = request
    resume = scenario.removeprefix("resume_") if scenario.startswith("resume_") else None
    goal_case = scenario in {"goal_pass", "goal_fail", "forged_evidence"} or resume in {"ready", "verifying", "committed"}
    doctor_ok = scenario == "success" or goal_case
    record, flags, _ = live
    monkeypatch.setattr(misc, "_HEAD_SHA", "3" * 40)  # Old live SHA differs from source base.
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store_fixture)
    store = SelfUpdateStore()
    # The listener fixture has no worker or session side effects. Replace its
    # example update with a fully frozen request at the public STAGING entry.
    store.transition(record.request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY)
    request = replace(record.request, update_id="su_handoff", session_id="p1",
                      origin_turn_id="u1", origin_assistant_id="a1")
    if goal_case:
        request = replace(request, goal="Diagnostics reports a working database", assertions=("database_ok is true",))
    monkeypatch.setattr("openprogram.agent.internals._model_tools.load_agent_profile",
                        lambda _: {"id": "main", "system_prompt": "frozen"})
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed"))
    turn = SimpleNamespace(agent_id="main", **authority.local_owner_authority())
    config = freeze_verifier_config(request, turn)
    request = replace(request, pre_update_evidence=(*request.pre_update_evidence, config_evidence(config)))
    store.create(request, verifier_config=config)
    store.transition(request.update_id, UpdatePhase.STAGING)
    update_dir = store.root / request.update_id
    installer = update_dir / "controller/install-app.sh"
    installer.parent.mkdir()
    installer.write_text("#!/bin/sh\nexit 0\n")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    artifact = update_dir / "artifact/OpenProgram.app"
    artifact.mkdir(parents=True)
    transaction = update_dir / "transaction"
    transaction.mkdir()
    if native_install:
        _, artifact, target, native_tmp = fixtures.getfixturevalue("installation")
        if resume == "ready":
            import shutil
            canonical = update_dir / "artifact/OpenProgram.app"
            shutil.copytree(artifact, canonical, dirs_exist_ok=True)
            artifact = canonical
        installer.write_bytes(INSTALLER.read_bytes())
        digest = hashlib.sha256(installer.read_bytes()).hexdigest()
        native_run = supervisor.subprocess.run
        def run(args, **kwargs):
            if args[:2] == ["/bin/bash", str(installer)]:
                kwargs["env"] = {**kwargs["env"], "DESTDIR": str(target.parent.parent),
                                 "HOME": str(native_tmp / "home"), "TMPDIR": str(native_tmp / "tmp"),
                                 "PATH": os.environ["PATH"]}
            return native_run(args, **kwargs)
        monkeypatch.setattr(supervisor.subprocess, "run", run)
        def validate(path):
            assert path.parent == target.parent and path.name.startswith(".openprogram-app-install.")
            assert path.is_dir() and not path.is_symlink()
            return path
        monkeypatch.setattr(supervisor, "_validate_transaction_path", validate)
    else:
        monkeypatch.setattr(supervisor, "_prepare_install", lambda *_: str(transaction))
        monkeypatch.setattr(supervisor, "_validate_transaction_path", lambda path: path)
    artifact_digest = supervisor._tree_digest(artifact)
    builds = []
    monkeypatch.setattr(supervisor, "_build_candidate", lambda *_: builds.append(1) or supervisor.Artifact(artifact, artifact_digest))
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda *_: True)
    if not goal_case:
        monkeypatch.setattr(supervisor, "_finish_verification", lambda *_: 0)  # System-gate tests only.
    monkeypatch.setattr("openprogram.agent.job.runner._broadcast", lambda *a, **k: None)
    calls = []
    def execute(req):
        calls.append(req)
        if goal_case:
            import asyncio
            from openprogram.programs import get_agent_tool
            from openprogram.agent.turn_request_context import set_turn_request, reset_turn_request
            token = set_turn_request(req)
            try:
                output = asyncio.run(get_agent_tool("self_update_observe").execute("observe-live", {"entry": "/api/diagnostics"}, None, None))
                assert not output.is_error, output
                observation = json.loads(output.content[0].text)
                assert json.loads(observation["body"])["database_ok"] is True
                status = "fail" if scenario == "goal_fail" else "pass"
                reference = "forged" if scenario == "forged_evidence" else observation["evidence_ref"]
                return TurnResult(json.dumps(dict(schema=1, update_id=request.update_id, candidate_sha=request.candidate_sha,
                    attempt=1, verdict=status, assertions=[dict(id="acceptance-1", status=status, entry=observation["entry"],
                    observation="Actual authenticated database check", evidence_refs=[reference], observed_at=observation["observed_at"])])), "verify_u", "verify_a")
            finally:
                reset_turn_request(token)
        return TurnResult("inconclusive", "verify_u", "verify_a")
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", execute)
    runner = JobRunner(max_workers=1)
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    startup_result = []
    thread = threading.Thread(target=lambda: startup_result.append(recovery.recover_pending_updates()))
    old_startup_result = []
    old_thread = threading.Thread(target=lambda: old_startup_result.append(recovery.recover_pending_updates()))
    native_activate = supervisor._activate
    interrupted = False
    original_transition = SelfUpdateStore.transition
    def transition(self, update_id, target_phase, **kwargs):
        nonlocal interrupted
        state = original_transition(self, update_id, target_phase, **kwargs)
        if (update_id == request.update_id and not interrupted
            and ((resume == "ready" and target_phase is UpdatePhase.READY)
                 or (resume == "verifying" and target_phase is UpdatePhase.VERIFYING))):
            interrupted = True
            raise SystemExit("controller interrupted")
        return state
    monkeypatch.setattr(SelfUpdateStore, "transition", transition)
    def activate(*args):
        nonlocal transaction, interrupted
        transaction = Path(args[0])
        assert store.load(request.update_id).state.phase is UpdatePhase.ACTIVATING
        if native_install:
            native_activate(*args)
            assert version(target) == "0.6.2"
        flags["doctor"] = doctor_ok
        monkeypatch.setattr(misc, "_HEAD_SHA", "2" * 40)
        thread.start()  # As with installation, Web is serving before recovery.
        if resume == "activating" and not interrupted:
            interrupted = True
            raise SystemExit("controller interrupted")
        return str(transaction)
    monkeypatch.setattr(supervisor, "_activate", activate)
    native_installer = supervisor._installer_command
    def installer_command(argument, directory, sha, mode):
        nonlocal interrupted
        if mode == "--commit":
            reported = native_installer(argument, directory, sha, mode) if native_install else str(transaction)
            if resume == "committed" and not interrupted:
                interrupted = True
                raise SystemExit("controller interrupted")
            return reported
        if mode != "--rollback":
            return native_installer(argument, directory, sha, mode)
        assert (update_dir / "rollback-1.json").is_file()
        if native_install:
            native_installer(argument, directory, sha, mode)
            assert version(target) == "0.6.1" and phase(transaction) == "rolled_back"
        flags["doctor"] = True
        monkeypatch.setattr(misc, "_HEAD_SHA", ("4" if scenario == "wrong_restored" else "3") * 40)
        old_thread.start()
        return str(transaction)
    monkeypatch.setattr(supervisor, "_installer_command", installer_command)
    try:
        if resume:
            with pytest.raises(SystemExit, match="controller interrupted"):
                supervisor.run_supervisor(request.update_id, state_root=store.root, installer_sha256=digest)
            assert interrupted
            assert store.load(request.update_id).state.phase is {
                "ready": UpdatePhase.READY, "activating": UpdatePhase.ACTIVATING,
                "verifying": UpdatePhase.VERIFYING, "committed": UpdatePhase.VERIFYING,
            }[resume]
        result = supervisor.run_supervisor(request.update_id, state_root=store.root, installer_sha256=digest)
        if resume:
            assert store.load(request.update_id).state.phase is (UpdatePhase.ROLLED_BACK if resume == "activating" else UpdatePhase.SUCCEEDED)
            assert builds == [1]
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert startup_result == [scenario != "wrong_restored"]
        current = store.load(request.update_id)
        assert current.state.detail["transaction_dir"] == str(transaction)
        if scenario in {"success", "goal_pass"} or resume in {"ready", "verifying", "committed"}:
            assert result == 0 and current.state.phase is (UpdatePhase.SUCCEEDED if goal_case else UpdatePhase.VERIFYING)
            recovery._check_gate(current)
            job_id = f"self-update:{request.update_id}:verify:1"
            job = runner.await_job(job_id, timeout=5)
            assert job is not None and job.status is JobStatus.COMPLETED, job
            assert recovery.recover_pending_updates() is True
            assert len(calls) == 1 and len(runner.list_jobs("p1")) == 1
            assert calls[0].source == "self_update_verify"
            assert calls[0].model_override == "fake/fixed"
            assert calls[0].branch_from is None and calls[0].advance_head is False
            if goal_case:
                assert current.state.detail["verifier_verdict"] == "pass"
                assert not (store.root / "maintenance.json").exists()
                if native_install:
                    assert phase(transaction) == "committed" and version(target) == "0.6.2"
                    assert not (transaction / "previous.app").exists()
        else:
            old_thread.join(timeout=5)
            assert not old_thread.is_alive() and old_startup_result == [scenario != "wrong_restored"]
            expected_phase = UpdatePhase.NEEDS_MANUAL_RECOVERY if scenario == "wrong_restored" else UpdatePhase.ROLLED_BACK
            assert result == 1 and current.state.phase is expected_phase
            if goal_case:
                verdict = "fail" if scenario == "goal_fail" else "inconclusive"
                assert current.state.detail["verifier_verdict"] == verdict
                assert current.state.detail["error"] == f"verifier result: {verdict}"
            else:
                expected_error = "controller interrupted during activation" if resume == "activating" else "system probe failed: doctor"
                assert current.state.detail["error"] == expected_error
            assert current.state.detail["previous_system_gate"]["candidate_sha"] == "3" * 40
            if scenario == "wrong_restored":
                assert "recovery_error" in current.state.detail
                assert "restored_system_gate" not in current.state.detail
            else:
                assert current.state.detail["restored_system_gate"]["candidate_sha"] == "3" * 40
            assert (store.root / "maintenance.json").exists() is (scenario == "wrong_restored")
            if goal_case:
                assert len(calls) == 1 and len(runner.list_jobs("p1")) == 1
            else:
                assert "system_gate" not in current.state.detail
                assert runner.list_jobs("p1") == [] and calls == []
    finally:
        if thread.is_alive() or old_thread.is_alive():
            from openprogram.self_update.types import TERMINAL_PHASES
            if store.load(request.update_id).state.phase not in TERMINAL_PHASES:
                store.transition(request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY)
            for started in (thread, old_thread):
                if started.ident is not None:
                    started.join(timeout=5)
        runner.shutdown()
