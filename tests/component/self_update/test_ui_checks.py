"""Main-window evidence crosses the approved tool, Job and native transport."""
import asyncio
import base64
import hashlib
import json
import os
import threading
import time
import struct
import zlib
from types import SimpleNamespace

import httpx
import pytest

from tests.component.programs.test_self_update_tools import _isolated_owner  # noqa: F401
from tests.component.self_update.test_verification_plan import _plan, _public_prepare
from tests.component.self_update.test_native_checks import installed_cli  # noqa: F401
from tests.component.self_update.test_package_protocol import package_factory  # noqa: F401
from tests.component.self_update.test_system_probe import live as http_live  # noqa: F401
from tests.component.self_update.test_verification_channel import consume, store_fixture, verifier  # noqa: F401


@pytest.fixture
def ui_install(package_factory, installed_cli, monkeypatch):
    from openprogram.self_update import package_protocol, ui_checks
    from openprogram.webui import server
    from openprogram.webui.routes import misc, self_updates
    from openprogram.webui.ws_actions import webtab
    app = package_factory(ui=True)
    actual_package = package_protocol.validate_ui_package
    monkeypatch.setattr(package_protocol, "validate_ui_package", lambda _: actual_package(app))
    monkeypatch.setattr(ui_checks, "_process_identity", lambda identity: {"app_pid": identity["app_pid"], "renderer_pid": identity["renderer_pid"]})
    register = misc.register
    def routes(app):
        register(app)
        self_updates.register(app)
    monkeypatch.setattr(misc, "register", routes)
    control = {}
    def capture():
        state = control["state"]
        url = f"http://127.0.0.1:{state.port}/api/self-updates/su_channel/desktop-verification/{control['nonce']}"
        headers = {"Authorization": f"Bearer {state.token}"}
        with httpx.Client(trust_env=False, timeout=5) as client:
            control["unauthorized"] = client.get(url).status_code
            response = client.get(url, headers=headers)
            control["claim_status"] = response.status_code
            if response.status_code != 200:
                return False
            contract = response.json()
            control["duplicate_claim"] = client.get(url, headers=headers).status_code
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
            raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * 48) * 16)) + chunk(b"IEND", b""))
            body = {k: contract[k] for k in ("schema", "nonce", "update_id", "attempt", "check_id", "worker_pid")}
            body.update(identity=dict(app_path="/Applications/OpenProgram.app", app_pid=55, renderer_pid=66,
                candidate_sha=contract["candidate_sha"], window_id=1, web_contents_id=2, target_id="main-target",
                route="/s/p1", bounds=dict(x=0, y=0, width=16, height=16)), observed_at=time.time(),
                screenshot=dict(mime_type="image/png", width=16, height=16, sha256=hashlib.sha256(raw).hexdigest(),
                    data=base64.b64encode(raw).decode()), accessibility={"nodes": [{"nodeId": "1", "role": {"value": "RootWebArea"}}]},
                cleanup_complete=True)
            if control.get("mutation"):
                control["mutation"](body)
            response = client.post(url, headers=headers, json=body)
            control["post_status"] = response.status_code
            control["duplicate_post"] = client.post(url, headers=headers, json=body).status_code
            return response.status_code == 200
    class Socket:
        async def send_text(self, payload):
            data = json.loads(payload)["data"]
            assert data["op"] == "self_update_capture" and data["window_id"] == "main"
            assert set(data) == {"op", "window_id", "nonce", "req_id"}
            control["nonce"] = data["nonce"]
            ok = await asyncio.to_thread(capture)
            await webtab.handle_webtab_result(self, {"req_id": data["req_id"], "ok": ok, "window_id": "main"})
    ws = Socket()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    monkeypatch.setattr(server, "_loop", loop)
    monkeypatch.setattr(server, "_ws_connections", {ws})
    asyncio.run(webtab.handle_webtab_register(ws, {"window_id": "main"}))
    try:
        yield control
    finally:
        webtab.release_connection(ws)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


@pytest.fixture
def live(ui_install, http_live, monkeypatch):
    from openprogram.agent.authority import owner_principal_id
    # The HTTP fixture fixes owner identity; align the tool's imported alias.
    monkeypatch.setattr("openprogram.programs.tools.system.self_update.owner_principal_id", owner_principal_id)
    ui_install["state"] = http_live[2]
    return http_live


def _ui_plan():
    plan = _plan()
    plan["checks"][0].update(entry="ui:main", max_output_bytes=1048576)
    return plan


def test_public_prepare_accepts_main_window_capture_plan(tmp_path, monkeypatch, ui_install):
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed", input=["text", "image"]))
    result, _ = _public_prepare(tmp_path, monkeypatch, _ui_plan())
    assert not result.is_error, result.content


def test_public_prepare_rejects_text_only_ui_verifier(tmp_path, monkeypatch, ui_install):
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed", input=["text"]))
    result, store = _public_prepare(tmp_path, monkeypatch, _ui_plan())
    assert result.is_error
    assert "image" in result.content[0].text
    assert not store.root.exists()


@pytest.mark.parametrize("verifier", [_ui_plan()], indirect=True)
def test_restart_rejects_lost_image_capability_before_job_admission(verifier, monkeypatch):
    from openprogram.self_update.recovery import recover_pending_updates
    from openprogram.agent.job.store import load_job
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed", input=["text"]))
    assert recover_pending_updates() is False
    assert load_job(verifier.request.session_id, verifier.grant["job_id"]) is None
    error = verifier.store.root / verifier.request.update_id / "startup-error-1.json"
    assert error.is_file()


@pytest.mark.parametrize("verifier", [_ui_plan()], indirect=True)
def test_restored_queue_checks_image_capability_before_model_execution(verifier, monkeypatch):
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.self_update.verifier_config import load_verifier_config, verifier_prompt
    record = verifier.store.load(verifier.request.update_id)
    config = load_verifier_config(verifier.store, record)
    claim = verifier.store.claim_verifier(record.request.update_id, owner=f"worker:{os.getpid()}", lease_seconds=15)
    verifier.runner.spawn_job(job_id=claim.job_id, session_id=record.request.session_id,
        prompt=verifier_prompt(record, config), agent_id=config["agent_id"], source="self_update_verify",
        context_mode="clean", parent_msg_id=None, caller_msg_id=record.request.origin_assistant_id,
        spawn_caller=record.request.origin_assistant_id, advance_head=False, wait=True,
        creates_agent=False, defer_dispatch=True, **{key: config[key] for key in (
            "profile_snapshot", "model_override", "tools_override", "response_format", "authority")})
    verifier.runner.shutdown()
    with verifier.runner._governor.ledger.immediate() as conn:
        conn.execute("UPDATE job_admissions SET dispatch_ready = 1 WHERE job_id = ?", (claim.job_id,))
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed", input=["text"]))
    recovered = JobRunner(max_workers=1, governor=verifier.runner._governor)
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: recovered)
    try:
        job = recovered.await_job(claim.job_id, timeout=5)
        assert job is not None and job.status is JobStatus.ERRORED
        assert "image" in job.error
        assert "tool_result" not in verifier.control
    finally:
        recovered.shutdown()


@pytest.mark.parametrize("verifier", [_ui_plan()], indirect=True)
def test_registered_ui_observer_returns_actual_image_block(verifier, ui_install):
    from openprogram.self_update import ui_checks
    from openprogram.providers.types import ImageContent
    verifier.run()
    result = verifier.control["tool_result"]
    assert not result.is_error, (result, {key: value for key, value in ui_install.items() if key != "state"})
    assert len(result.content) == 2 and isinstance(result.content[1], ImageContent)
    assert result.content[1].mime_type == "image/png"
    assert "\"data\":" not in result.content[0].text
    assert consume(verifier)["verdict"] == "pass"
    assert ui_install["unauthorized"] == 401
    assert ui_install["duplicate_claim"] == ui_install["duplicate_post"] == 409
    assert not ui_checks._pending


@pytest.mark.parametrize("verifier", [_ui_plan()], indirect=True)
@pytest.mark.parametrize("mutation", [lambda b: b.update(cleanup_complete=False),
    lambda b: b["identity"].update(route="/s/other"), lambda b: b["screenshot"].update(sha256="0" * 64),
    lambda b: b.update(observed_at=0)])
def test_invalid_capture_cannot_pass(verifier, ui_install, mutation):
    from openprogram.self_update import ui_checks
    ui_install["mutation"] = mutation
    verifier.run()
    assert verifier.control["tool_result"].is_error
    assert consume(verifier)["verdict"] == "inconclusive"
    assert ui_install["post_status"] == 409
    assert not ui_checks._pending


def test_packaged_ui_capability_is_bound_to_actual_files(package_factory):
    from openprogram.self_update.package_protocol import validate_ui_package
    with pytest.raises(ValueError, match="UI verification"):
        validate_ui_package(package_factory("old"))
    app = package_factory("new", ui=True)
    manifest = validate_ui_package(app)
    backend = app / "Contents/Resources" / manifest["bindings"]["backend"]["path"]
    backend.write_text(backend.read_text() + "\n# changed after packaging\n")
    with pytest.raises(ValueError, match="UI verification"):
        validate_ui_package(app)


@pytest.mark.parametrize("verifier", [_ui_plan()], indirect=True)
def test_cancelled_verifier_cannot_upload_capture(verifier, ui_install):
    from openprogram.self_update import ui_checks
    ui_install["mutation"] = lambda _: verifier.runner.cancel_job(verifier.grant["job_id"], reason="owner stop")
    verifier.run()
    assert consume(verifier)["verdict"] == "inconclusive"
    assert ui_install["post_status"] == 409
    assert not ui_checks._pending
