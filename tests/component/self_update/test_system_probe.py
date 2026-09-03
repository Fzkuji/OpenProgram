"""Real owner-auth HTTP and WebSocket observations, without another worker."""
from dataclasses import replace
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

from openprogram.self_update import SelfUpdateStore, UpdateRequest, UpdatePhase


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
    @app.get("/chat")
    async def chat():
        return HTMLResponse('<script src="/_next/test.js"></script>' if flags["web"] else "unavailable", status_code=200 if flags["web"] else 503)
    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_text(json.dumps({"type": "functions_list"}))
        assert await ws.receive_text() == "ping"
        await ws.send_text(json.dumps({"type": "pong" if flags["ws"] else "failure"}))
        await ws.close()
    state = OwnerAuthState.start(state_dir=profile, bind_host="127.0.0.1", port=port, allowed_origins=(), raw_token=bytes(range(32)), owner_principal_id=owner)
    monkeypatch.setattr(listener, "_app", lambda _: OwnerAuthMiddleware(app, auth_state=state))
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
