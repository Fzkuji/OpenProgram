"""Real verifier Job/tool/HTTP evidence and durable result consumption."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.component.self_update.test_system_probe import live  # noqa: F401
from openprogram.self_update import SelfUpdateStore, UpdatePhase
from openprogram.self_update import verification_channel as channel


@pytest.fixture
def verifier(store_fixture, live, monkeypatch, request):
    from openprogram.agent import authority
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.turn_request_context import set_turn_request, reset_turn_request
    from openprogram.programs import get_agent_tool
    from openprogram.self_update.system_probe import probe_system
    from openprogram.self_update.verifier_config import freeze_verifier_config, config_evidence
    from openprogram.self_update.recovery import recover_pending_updates

    monkeypatch.setattr("openprogram.self_update.launcher.launch_supervisor", lambda *_a, **_k: None)

    plan = getattr(request, "param", None)
    original, flags, _ = live
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store_fixture)
    monkeypatch.setattr("openprogram.agent.internals._model_tools.load_agent_profile", lambda _: {"id": "main", "system_prompt": "verify"})
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model", lambda *a: SimpleNamespace(provider="fake", id="fixed"))
    monkeypatch.setattr("openprogram.agent.job.runner._broadcast", lambda *a, **k: None)
    store = SelfUpdateStore()
    store.transition(original.request.update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY)
    request = replace(original.request, update_id="su_channel", session_id="p1", origin_turn_id="u1", origin_assistant_id="a1",
                      goal="Diagnostics should report a working database",
                      assertions=("Diagnostics reports database_ok=true",) * (len(plan["checks"]) if plan else 1))
    config = freeze_verifier_config(request, SimpleNamespace(agent_id="main", **authority.local_owner_authority()),
                                    verification_plan=plan)
    request = replace(request, pre_update_evidence=(*request.pre_update_evidence, config_evidence(config)))
    store.create(request, verifier_config=config)
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(request.update_id, phase)
    gate = probe_system(store.load(request.update_id))
    grant = channel.issue_grant(store, request.update_id, gate)
    store.transition(request.update_id, UpdatePhase.VERIFYING,
                     detail={"system_gate": gate, "verifier_grant_sha256": channel._digest(grant),
                             "previous_system_gate": {"candidate_sha": "3" * 40}})
    control = {"status": "pass", "entry": "/api/diagnostics"}
    def dispatch(req):
        control["prompt"] = req.user_text
        token = set_turn_request(req)
        try:
            if control.get("read_grant"):
                path = store.root / request.update_id / "verifier-grant-1.json"
                name = control["read_tool"]
                args = {"read": {"file_path": str(path)}, "grep": {"pattern": "token", "path": str(path)},
                        "glob": {"pattern": "**/*.json", "path": str(path.parent)}, "list": {"path": str(path.parent)}}[name]
                control["read_result"] = asyncio.run(get_agent_tool(name).execute("read-grant", args, None, None))
                return TurnResult("inconclusive", "verify_u", "verify_a")
            if control.get("cancel"):
                runner.cancel_job(grant["job_id"], reason="test cancellation")
            tool = get_agent_tool("self_update_observe")
            args = control.get("args", {"check_id": plan["checks"][0]["id"]} if plan else {"entry": control["entry"]})
            output = asyncio.run(tool.execute("observe-call", args, None, None))
            control["tool_result"] = output
            if output.is_error:
                return TurnResult("inconclusive", "verify_u", "verify_a")
            observed = json.loads(output.content[0].text)
            control["observed"] = observed
            assert json.loads(observed["body"])["database_ok"] is True
            row = dict(id="acceptance-1", status=control["status"], entry=observed["entry"],
                       observation="The authenticated response reports database_ok=true",
                       evidence_refs=[control.get("reference", observed["evidence_ref"])], observed_at=observed["observed_at"])
            row.update(control.get("row_changes", {}))
            return TurnResult(json.dumps(dict(schema=1, update_id=request.update_id, candidate_sha=request.candidate_sha,
                                             attempt=1, verdict=control["status"], assertions=[row])), "verify_u", "verify_a")
        finally:
            reset_turn_request(token)
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", dispatch)
    runner = JobRunner(max_workers=1)
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    def run():
        assert recover_pending_updates() is True
        job = runner.await_job(grant["job_id"], timeout=10)
        assert job is not None, "verifier did not finish"
        return job
    yield SimpleNamespace(store=store, request=request, grant=grant, runner=runner, control=control, run=run, flags=flags)
    runner.shutdown()


def consume(v, token=None):
    return channel.consume_result(v.store, v.request.update_id, token if token is not None else v.grant["token"])


@pytest.mark.parametrize("verdict", ["pass", "fail", "inconclusive"])
def test_registered_observer_and_durable_job_result_are_bound(verifier, verdict):
    v = verifier
    v.control["status"] = verdict
    job = v.run()
    assert v.control["tool_result"].is_error is False, v.control["tool_result"]
    receipt = consume(v)
    assert receipt["verdict"] == verdict, receipt
    assert consume(v) == receipt
    assert v.grant["token"] not in job.result_text
    assert v.grant["token"] not in json.dumps(v.control["observed"])
    assert v.grant["token"] not in json.dumps(receipt)
    from openprogram.self_update.projection import read_status
    projected = read_status(v.store, session_id=v.request.session_id, update_id=v.request.update_id)
    assert projected["verifier_verdict"] == verdict
    assert projected["verifier"]["assertions"][0]["evidence_refs"] == [v.control["observed"]["evidence_ref"]]
    assert v.grant["token"] not in json.dumps(projected)


def test_public_evidence_read_is_bound_to_owner_session_and_signed_receipt(verifier, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from openprogram.agent.authority import owner_principal_id
    from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.webui.routes import self_updates
    from openprogram.self_update.projection import read_status

    # The live fixture fixes the owner function; keep the tool module's
    # already-imported alias on that same identity without bypassing checks.
    monkeypatch.setattr("openprogram.programs.tools.system.self_update.owner_principal_id", owner_principal_id)
    v = verifier
    v.run()
    consume(v)
    auth = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id=owner_principal_id(),
                                        bind_host="127.0.0.1", port=18100, allowed_origins=())
    app = FastAPI()
    app.add_middleware(OwnerAuthMiddleware, auth_state=auth)
    self_updates.register(app)
    projection = read_status(v.store, session_id="p1", update_id=v.request.update_id)
    params = {"session_id": "p1", "evidence_id": projection["verifier"]["evidence_id"]}
    url = f"/api/self-updates/{v.request.update_id}/evidence"
    headers = {"Authorization": f"Bearer {auth.token}"}
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in v.store.root.rglob("*") if p.is_file()}
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 12345)) as client:
        assert client.get(url, params=params).status_code == 401
        assert client.get(url, params={**params, "session_id": "other"}, headers=headers).status_code == 403
        response = client.get(url, params=params, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        evidence = response.json()
        assert evidence["verdict"] == "pass"
        assert evidence["observations"][0]["entry"] == "/api/diagnostics"
        assert json.loads(evidence["observations"][0]["body"])["database_ok"] is True
        assert v.grant["token"] not in response.text and "signature" not in response.text
        ref = v.control["observed"]["evidence_ref"]
        assert client.get(url, params={**params, "evidence_id": ref}, headers=headers).status_code == 200
        assert client.get(url, params={**params, "evidence_id": "../../verifier-grant-1.json"}, headers=headers).status_code == 404
        assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in v.store.root.rglob("*") if p.is_file()} == before
        path = next((v.store.root / v.request.update_id / "observations").iterdir())
        v.store._write_json(path, {"observation": {"body": "forged"}})
        assert client.get(url, params=params, headers=headers).status_code == 409


@pytest.mark.parametrize("mutation", ["foreign_ref", "stale_time", "wrong_entry", "changed_file"])
def test_unresolved_or_changed_evidence_cannot_pass(verifier, mutation):
    v = verifier
    if mutation == "foreign_ref":
        v.control["reference"] = "observation:" + "0" * 32 + ":" + "1" * 64
    if mutation == "stale_time":
        v.control["row_changes"] = {"observed_at": 0}
    if mutation == "wrong_entry":
        v.control["row_changes"] = {"entry": "/api/config"}
    v.run()
    if mutation == "changed_file":
        path = next((v.store.root / v.request.update_id / "observations").iterdir())
        evidence = channel._read(path)
        evidence["observation"]["body"] = "forged"
        v.store._write_json(path, evidence)
    assert consume(v)["verdict"] == "inconclusive"


def test_wrong_token_and_changed_terminal_result_cannot_reuse_authorization(verifier):
    from openprogram.agent.job.store import update_job_status
    from openprogram.agent.job.types import JobStatus
    v = verifier
    v.run()
    with pytest.raises(ValueError, match="authorization"):
        consume(v, "wrong")
    assert consume(v)["verdict"] == "pass"
    update_job_status("p1", v.grant["job_id"], JobStatus.COMPLETED, result_text="changed")
    with pytest.raises(ValueError, match="already consumed"):
        consume(v)


def test_modified_result_receipt_cannot_be_accepted(verifier):
    v = verifier
    v.control["status"] = "fail"
    v.run()
    receipt = consume(v)
    receipt["verdict"] = "pass"
    v.store._write_json(v.store.root / v.request.update_id / "verifier-result-1.json", receipt)
    with pytest.raises(ValueError, match="signature"):
        consume(v)


@pytest.mark.parametrize("entry", ["http://example.com", "/api/config", "/api/commands?reload=1", "/../api/diagnostics"])
def test_observer_does_not_accept_unreviewed_entries(verifier, entry):
    v = verifier
    v.control["entry"] = entry
    v.run()
    assert v.control["tool_result"].is_error
    assert not (v.store.root / v.request.update_id / "observations").exists()
    assert consume(v)["verdict"] == "inconclusive"


def test_rollback_during_network_observation_cannot_publish_evidence(verifier, monkeypatch):
    from openprogram.self_update import system_probe
    from openprogram.self_update.rollback_intent import begin_rollback
    v = verifier
    original = system_probe.observe_system
    def observe_then_restore(*args):
        result = original(*args)
        begin_rollback(v.store, v.request.update_id, "test failure")
        return result
    monkeypatch.setattr(system_probe, "observe_system", observe_then_restore)
    v.run()
    assert v.control["tool_result"].is_error
    assert not (v.store.root / v.request.update_id / "observations").exists()
    with pytest.raises(ValueError, match="not active"):
        consume(v)


def test_observer_requires_real_running_job_context(verifier):
    from openprogram.programs import get_agent_tool
    result = asyncio.run(get_agent_tool("self_update_observe").execute("outside", {"entry": "/api/diagnostics"}, None, None))
    assert result.is_error
    assert consume(verifier) is None  # Missing Job is pending, not success.


@pytest.mark.parametrize("entry", ["/api/auth/challenge", "/api/diagnostics", "/api/commands"])
def test_observer_never_follows_redirects(verifier, entry):
    v = verifier
    v.flags["requests"].clear()
    v.flags["redirect_entry"] = entry
    v.control["entry"] = "/api/commands"
    v.run()
    assert v.flags["requests"].count(entry) == 1
    assert "/redirect-target" not in v.flags["requests"]
    assert v.control["tool_result"].is_error
    assert not (v.store.root / v.request.update_id / "observations").exists()
    assert consume(v)["verdict"] == "inconclusive"


@pytest.mark.parametrize("credential", ["owner", "grant"])
@pytest.mark.parametrize("field", ["body", "header"])
def test_echoed_credentials_never_reach_model_or_evidence(verifier, credential, field):
    from openprogram.backend_endpoint import read_web_token
    v = verifier
    secret = read_web_token() if credential == "owner" else v.grant["token"]
    v.flags.update(echo_secret="owner" if credential == "owner" else secret, echo_field=field)
    job = v.run()
    output = v.control["tool_result"]
    assert secret not in output.content[0].text
    assert output.is_error
    assert secret not in (job.result_text or "")
    assert not (v.store.root / v.request.update_id / "observations").exists()
    receipt = consume(v)
    assert receipt["verdict"] == "inconclusive"
    assert secret not in json.dumps(receipt)


@pytest.mark.parametrize("oversize", [300_000, 1_100_000])
def test_large_observation_or_system_response_does_not_publish_evidence(verifier, oversize):
    v = verifier
    v.flags["padding"] = "x" * oversize
    v.control["entry"] = "/chat"
    v.run()
    assert v.control["tool_result"].is_error
    assert not (v.store.root / v.request.update_id / "observations").exists()
    assert consume(v)["verdict"] == "inconclusive"


def test_cancelled_verifier_never_passes(verifier):
    v = verifier
    v.control["cancel"] = True
    v.run()
    assert consume(v)["verdict"] == "inconclusive"


def test_expired_grant_is_not_consumable(verifier, monkeypatch):
    import time
    v = verifier
    v.run()
    monkeypatch.setattr(channel, "time", SimpleNamespace(time=lambda: v.grant["deadline"] + 1))
    with pytest.raises(ValueError, match="deadline"):
        consume(v)
    assert time.time() < v.grant["deadline"]


def test_changed_job_identity_is_not_consumable(verifier):
    from openprogram.agent.job.store import update_job_status
    from openprogram.agent.job.types import JobStatus
    v = verifier
    v.run()
    update_job_status("p1", v.grant["job_id"], JobStatus.COMPLETED, source="agent_spawn")
    with pytest.raises(ValueError, match="frozen execution"):
        consume(v)


def test_symlink_evidence_cannot_pass(verifier, tmp_path):
    v = verifier
    v.run()
    path = next((v.store.root / v.request.update_id / "observations").iterdir())
    outside = tmp_path / "copied-evidence.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert consume(v)["verdict"] == "inconclusive"


def test_supervisor_times_out_a_missing_job_without_accepting_it(verifier, monkeypatch):
    import time
    from openprogram.self_update import supervisor
    v = verifier
    calls = []
    def rollback(store, update_id, digest, error, *, verdict=None):
        calls.append((str(error), verdict))
        store.transition(update_id, UpdatePhase.ROLLED_BACK)
    monkeypatch.setattr(supervisor, "_rollback", rollback)
    monkeypatch.setattr(supervisor, "leave_maintenance", lambda _: None)
    # Shorten only the controller's waiting budget; actual grant validation and
    # missing-Job reads still run. Physical rollback has native integration tests.
    deadline = {**v.grant, "deadline": time.time() + 0.01}
    assert supervisor._finish_verification(v.store, v.request.update_id, "a" * 64, deadline) == 1
    assert calls == [("verifier timed out", "inconclusive")]


@pytest.mark.parametrize("name", ["read", "grep", "glob", "list"])
def test_generic_read_cannot_expose_verifier_authorization_even_without_sandbox(verifier, monkeypatch, name):
    v = verifier
    v.control["read_grant"] = True
    v.control["read_tool"] = name
    monkeypatch.setattr("openprogram.sandbox.resolve_policy", lambda: None)
    v.run()
    output = v.control["read_result"].content[0].text
    assert "sandbox policy" in output
    assert v.grant["token"] not in output
