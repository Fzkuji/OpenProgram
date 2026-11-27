"""The runtime's maintenance gate: its answers with and without self-update."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime import maintenance


@pytest.fixture()
def no_gate(monkeypatch):
    monkeypatch.setattr(maintenance, "_hooks", {})
    monkeypatch.setattr(maintenance, "_load_attempted", True)


def test_without_a_gate_every_turn_and_job_is_admitted(no_gate):
    with maintenance.turn_admission("chat") as admitted:
        assert admitted is True
    assert maintenance.hold_turn(object()) is False
    assert maintenance.blocks("worker_restart") is False
    assert maintenance.open_state() is None
    claimed = []
    governor = SimpleNamespace(claim_next=lambda **kwargs: claimed.append(kwargs) or "job")
    assert maintenance.claim_job(governor, state=None, owner_instance_id="w",
                                 excluded_sessions=set(), only_job_id=None) == "job"
    assert claimed == [{"owner_instance_id": "w", "excluded_sessions": set(), "only_job_id": None}]
    assert maintenance.job_permission_snapshot(None, SimpleNamespace(source="cron"), "kept") == "kept"
    resumed = []
    maintenance.reconcile_jobs("runner", resumed.append)
    assert resumed == ["runner"]


def test_without_a_gate_an_update_stage_turn_is_refused(no_gate):
    with pytest.raises(RuntimeError):
        maintenance.require_execution("self_update_verify", session_id="s")
    with pytest.raises(RuntimeError):
        maintenance.approval_preview("u", "a" * 40, object())


def test_self_update_fills_every_hook(monkeypatch):
    from openprogram.operations.self_update import runtime_gate

    monkeypatch.setattr(maintenance, "_hooks", {})
    runtime_gate.register()
    assert set(maintenance._hooks) == maintenance.HOOKS


def test_a_briefly_held_update_state_reaches_the_runtime_as_busy(monkeypatch):
    from openprogram.operations.self_update import runtime_gate
    from openprogram.operations.self_update.types import ConcurrentUpdateError

    monkeypatch.setattr(maintenance, "_hooks", {})
    runtime_gate.register()

    def held():
        raise ConcurrentUpdateError("held")

    monkeypatch.setattr("openprogram.operations.self_update.delivery.delivery.deliver_pending", held)
    with pytest.raises(maintenance.StateBusy):
        maintenance.deliver_results()
