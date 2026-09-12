"""Unattended update follow-up is durable and bound to the original owner."""

from dataclasses import replace

import pytest

from tests.component.agent.async_job_support import store_fixture, fake_worker  # noqa: F401
from tests.unit.self_update.test_store import _request


def _prepared(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.agent.authority import owner_authority, owner_principal_id
    from openprogram.self_update import SelfUpdateStore, UpdatePhase
    from openprogram.self_update.continuation import config_evidence

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority = owner_authority(owner_principal_id())
    authority.update(speaker_kind="agent", interaction="background")
    config = dict(
        schema=1,
        agent_id="main",
        authority=authority,
        profile_snapshot={},
        model_override=None,
        tools_override=[],
        permission=dict(mode="ask", rules=None),
    )
    request = replace(
        _request(),
        session_id="p1",
        origin_assistant_id="a1",
        agent_id="main",
        pre_update_evidence=(config_evidence(config),),
    )
    updates = SelfUpdateStore()
    updates.create(request, continuation_config=config)
    updates.transition(request.update_id, UpdatePhase.ABORTED)
    return updates


def test_origin_continuation_dispatches_once_across_reconciliation(
    tmp_path, monkeypatch, store_fixture, fake_worker
):
    from openprogram.self_update.continuation import reconcile
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.store import load_job

    updates = _prepared(tmp_path, monkeypatch)
    runner = JobRunner(max_workers=1)
    try:
        reconcile(runner)
        assert fake_worker[3].wait(5)
        reconcile(runner)
        job = load_job("p1", "self-update:su_test:continue:1")
        assert job is not None
        assert job.parent_msg_id == "a1"
        assert job.advance_head is True
        assert job.source == "self_update_continue"
        assert job.interaction == "background"
        assert "Add behavior" in job.prompt or "goal" in job.prompt
        assert len(fake_worker[0]) == 1
        fake_worker[1].set()
        runner.await_job(job.id, timeout=5)
        reconcile(runner)
        assert len(runner.list_jobs("p1")) == 1
        assert (updates.root / "su_test" / "continuation-input.json").exists()
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_continuation_rejects_changed_owner(
    tmp_path, monkeypatch, store_fixture, fake_worker
):
    from openprogram.self_update.continuation import reconcile
    from openprogram.agent.job.runner import JobRunner

    _prepared(tmp_path, monkeypatch)
    runner = JobRunner(max_workers=1)
    monkeypatch.setattr(
        "openprogram.agent.authority.owner_principal_id",
        lambda: "owner/install/ffffffffffffffff",
    )
    try:
        with pytest.raises(ValueError, match="owner"):
            reconcile(runner)
        assert runner.list_jobs("p1") == []
    finally:
        fake_worker[1].set()
        runner.shutdown()
