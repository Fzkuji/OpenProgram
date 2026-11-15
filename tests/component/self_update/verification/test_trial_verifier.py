"""The verifier runs during a trial: in the live worker, observing the trial's port."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import time
import zlib

import pytest

from openprogram.operations.self_update.control import recovery
from openprogram.operations.self_update.store import SelfUpdateStore
from openprogram.operations.self_update.types import (
    InvalidTransitionError, UpdatePhase, UpdateRequest, VERIFIER_PHASES,
)
from openprogram.operations.self_update.verification import native_checks, system_probe, ui_checks, verification_channel
from tests.support.sealed_versions import BASE

CANDIDATE = "2" * 40


def _request(update_id="su_trial_v") -> UpdateRequest:
    return UpdateRequest(
        update_id=update_id, session_id="session-1", origin_turn_id="turn-1", origin_assistant_id="assistant-1",
        agent_id="main", repo=str(Path("/tmp/openprogram").resolve()), worktree_id="wt-1", base_sha="1" * 40,
        candidate_sha=CANDIDATE, changed_paths=("openprogram/example.py",), pre_update_evidence=("pytest:x",),
        goal="Goal", assertions=("Assertion",),
    )


@pytest.fixture
def store(tmp_path):
    return SelfUpdateStore(tmp_path / "self-updates")


def _in_trial(store) -> str:
    update_id = store.create(_request()).update_id
    store.transition(update_id, UpdatePhase.BUILDING)
    store.transition(update_id, UpdatePhase.TRIAL)
    return update_id


def _gate(store, update_id, *, worker_pid=4242) -> dict:
    return {"schema": 1, "candidate_sha": CANDIDATE, "attempt": store.load(update_id).state.attempt, "worker_pid": worker_pid,
            "verified_at": time.time(), "checks": {name: True for name in recovery.SYSTEM_CHECKS}}


def _publish(store, update_id, *, host_pid=None) -> dict:
    gate = _gate(store, update_id)
    grant = verification_channel.issue_grant(store, update_id, gate)
    store.record_detail(update_id, UpdatePhase.TRIAL, detail={
        "system_gate": gate, "verifier_grant_sha256": verification_channel._digest(grant),
        "trial": {"port": 40123, "state_dir": "/tmp/trial/.openprogram", "worker_pid": gate["worker_pid"],
                  "host_pid": os.getpid() if host_pid is None else host_pid, "version_dir": "/tmp/v"},
    })
    return grant


def test_the_verifier_phases_are_trial_and_verifying():
    assert VERIFIER_PHASES == {UpdatePhase.TRIAL, UpdatePhase.VERIFYING}


def test_a_trial_can_be_claimed_and_its_events_replay(store):
    update_id = _in_trial(store)
    claim = store.claim_verifier(update_id, owner="worker:1", lease_seconds=15)
    assert claim.acquired
    store.record_detail(update_id, UpdatePhase.TRIAL, detail={"trial": {"port": 1}})
    reloaded = SelfUpdateStore(store.root).load(update_id)
    assert reloaded.state.phase is UpdatePhase.TRIAL and reloaded.state.detail == {"trial": {"port": 1}}


def test_a_phase_without_a_verifier_cannot_be_claimed(store):
    update_id = store.create(_request()).update_id
    with pytest.raises(InvalidTransitionError):
        store.claim_verifier(update_id, owner="worker:1", lease_seconds=15)


def test_detail_is_recorded_only_during_a_trial(store):
    update_id = store.create(_request()).update_id
    with pytest.raises(ValueError):
        store.record_detail(update_id, UpdatePhase.READY, detail={})


def test_a_grant_issued_in_trial_loads_and_lasts_fifteen_minutes(store):
    update_id = _in_trial(store)
    grant = _publish(store, update_id)
    assert verification_channel.load_grant(store, store.load(update_id)) == grant
    assert grant["deadline"] - grant["issued_at"] == pytest.approx(min(900, 1800), abs=2)


def test_the_gate_names_the_trial_worker_and_the_job_this_worker(store):
    update_id = _in_trial(store)
    grant = _publish(store, update_id)
    record = store.load(update_id)
    recovery._check_gate(record)  # worker 4242 is the trial's, not this process.
    assert verification_channel.host_pid(record, grant) == os.getpid()


def test_observation_targets_the_trial_port(store):
    update_id = _in_trial(store)
    _publish(store, update_id)
    target = system_probe.trial_target(store.load(update_id))
    assert target.port == 40123 and target.state_dir == Path("/tmp/trial/.openprogram")
    assert system_probe.trial_target(store.load(update_id).__class__(
        request=store.load(update_id).request,
        state=store.load(update_id).state.__class__(
            **{**store.load(update_id).state.__dict__, "phase": UpdatePhase.VERIFYING},
        ),
    )) is None


def test_a_trial_without_a_target_refuses_to_observe(store):
    with pytest.raises(system_probe.SystemProbeError):
        system_probe.trial_target(store.load(_in_trial(store)))


def test_the_live_worker_admits_the_trial_verifier(store, monkeypatch):
    update_id = _in_trial(store)
    _publish(store, update_id)
    called = []
    monkeypatch.setattr(recovery, "SelfUpdateStore", lambda: store)
    monkeypatch.setattr(recovery, "_admit_verifier", lambda _store, record: called.append(record.state.phase) or True)
    assert recovery.dispatch_hot_update(update_id) is True
    assert called == [UpdatePhase.TRIAL]


def test_a_sealed_version_gives_the_same_identity_receipt(tmp_path):
    version = tmp_path / "v1"
    python = version / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("python")
    package = version / "venv" / "lib" / "python3.12" / "site-packages" / "openprogram"
    package.mkdir(parents=True)
    (package / "__main__.py").write_text("main")
    (version / "version.json").write_text(json.dumps({"commit": CANDIDATE, "base": BASE}))
    identity = native_checks.version_identity(version, expected_revision=CANDIDATE)
    assert identity["revision"] == CANDIDATE and identity["prefix"] == str(version / "venv")
    assert set(identity) == {"python", "prefix", "revision", "python_sha256", "manifest_sha256", "cli_sha256"}
    with pytest.raises(ValueError):
        native_checks.version_identity(version, expected_revision="3" * 40)


def _png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))


def _capture_contract():
    now = time.time()
    return dict(schema=ui_checks.UI_PROTOCOL, nonce="n" * 64, update_id="su_x", attempt=1, session_id="s", candidate_sha=CANDIDATE,
                worker_pid=9, check_id="c", issued_at=now - 5, deadline=now + 60, max_output_bytes=1_000_000, trial_port=4000)


def _capture(contract, **identity):
    raw = _png()
    return dict(schema=ui_checks.UI_PROTOCOL, nonce=contract["nonce"], update_id="su_x", attempt=1, check_id="c", worker_pid=9,
                observed_at=time.time(), cleanup_complete=True,
                identity={"trial_port": 4000, "candidate_sha": CANDIDATE, "route": "/chat", **identity},
                screenshot=dict(mime_type="image/png", width=1, height=1, sha256=hashlib.sha256(raw).hexdigest(),
                                data=base64.b64encode(raw).decode()),
                accessibility=dict(nodes=[{"role": "textarea", "name": ""}]))


def test_a_trial_page_capture_is_accepted_only_for_the_trial_port():
    contract = _capture_contract()
    ui_checks.validate_capture(_capture(contract), contract)
    with pytest.raises(ValueError):
        ui_checks.validate_capture(_capture(contract, trial_port=18100), contract)


def test_a_trial_refuses_a_ui_interaction(store):
    update_id = _in_trial(store)
    grant = _publish(store, update_id)
    with pytest.raises(ValueError, match="page only"):
        ui_checks._observe_trial_page(store, store.load(update_id), {"id": "c", "timeout_seconds": 5, "interaction": {"kind": "scroll"}}, grant)


def test_the_verifier_observes_the_trial_and_runs_in_the_live_worker(store, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from openprogram.operations.self_update.trial import verifier

    update_id = _in_trial(store)
    target = system_probe.ProbeTarget(port=40123, state_dir=tmp_path / "state")
    live = system_probe.ProbeTarget(port=18100, state_dir=tmp_path / "live")
    instance = SimpleNamespace(target=lambda _baseline: target, version="v1",
                               layout=SimpleNamespace(version=lambda version: tmp_path / version))
    monkeypatch.setattr(verifier, "probe_instance", lambda record, seen, **_: _gate(store, update_id))
    dispatched = []
    monkeypatch.setattr(verifier, "request_dispatch", lambda record, target: dispatched.append((record.state.phase, target)))
    monkeypatch.setattr(verification_channel, "consume_result", lambda *_: {"verdict": "pass"})
    assert verifier.run_verifier(store, store.load(update_id), instance, live=live, host_pid=77)["verdict"] == "pass"
    assert dispatched == [(UpdatePhase.TRIAL, live)]
    detail = store.load(update_id).state.detail["trial"]
    assert detail["port"] == 40123 and detail["host_pid"] == 77 and detail["worker_pid"] == 4242


def test_a_rejected_verifier_fails_the_trial(store, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from openprogram.operations.self_update.trial import instance as instance_module, verifier

    update_id = _in_trial(store)
    target = system_probe.ProbeTarget(port=40123, state_dir=tmp_path / "state")
    instance = SimpleNamespace(target=lambda _baseline: target, version="v1",
                               layout=SimpleNamespace(version=lambda version: tmp_path / version))
    monkeypatch.setattr(verifier, "probe_instance", lambda record, seen, **_: _gate(store, update_id))
    monkeypatch.setattr(verifier, "request_dispatch", lambda record, target: None)
    monkeypatch.setattr(verification_channel, "consume_result", lambda *_: {"verdict": "fail", "reason": "no"})
    with pytest.raises(instance_module.TrialError, match="did not pass"):
        verifier.run_verifier(store, store.load(update_id), instance, live=target, host_pid=77)
