"""Deterministic update results through the existing worker JobRunner."""
from dataclasses import replace

import pytest

from tests.component.agent.async_job_support import store_fixture  # noqa: F401
from tests.unit.self_update.test_store import _request


def test_runner_delivers_terminal_result_without_verifier(store_fixture, tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.agent.job.runner import JobRunner
    from openprogram.self_update import SelfUpdateStore, UpdatePhase
    from openprogram.store import SessionStore

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr("openprogram.agent.job.runner._broadcast", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", lambda *a, **k: calls.append(a))
    updates = SelfUpdateStore()
    request = replace(_request(), session_id="p1", origin_assistant_id="a1")
    updates.create(request)
    updates.transition(request.update_id, UpdatePhase.ABORTED)
    original_head = store_fixture._open("p1")[1].head_id

    runner = JobRunner(max_workers=1)
    try:
        runner._reconcile_resources()
        runner._reconcile_resources()
        fresh = SessionStore(store_fixture.root_path)
        pair = fresh._open("p1")
        results = [n for n in pair[1].nodes_by_seq
                   if n.metadata.get("source") == "self_update_result"]
        assert len(results) == 1, "A terminal update must reach its session without a verifier or client"
        assert results[0].metadata["self_update"]["phase"] == "aborted"
        assert results[0].caller == "a1"
        assert pair[1].head_id == original_head
        assert runner.list_jobs("p1") == []
        assert calls == [], "Result notification must not start an LLM turn"
    finally:
        runner.shutdown()


@pytest.mark.parametrize("failure", ["history", "receipt"])
def test_runner_retries_interrupted_delivery_once(store_fixture, tmp_path, monkeypatch, failure):
    from openprogram import paths
    from openprogram.agent.job.runner import JobRunner
    from openprogram.self_update import SelfUpdateStore, UpdatePhase
    from openprogram.store import SessionStore
    from openprogram.store.session.git_session import GitSession

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr("openprogram.agent.job.runner._broadcast", lambda *a, **k: None)
    updates = SelfUpdateStore()
    updates.create(replace(_request(), session_id="p1", origin_assistant_id="a1"))
    updates.transition("su_test", UpdatePhase.ABORTED)
    original_write = GitSession.write_history if failure == "history" else SelfUpdateStore._write_json
    failures = []

    def interrupted_write(self, *args, **kwargs):
        relevant = (args[-1].get("metadata", {}).get("source") == "self_update_result"
                    if failure == "history" else args[0].name == "delivery.json")
        if relevant and not failures:
            failures.append(failure)
            raise OSError("interrupted result persistence")
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(GitSession if failure == "history" else SelfUpdateStore,
                        "write_history" if failure == "history" else "_write_json", interrupted_write)
    runner = JobRunner(max_workers=1)
    try:
        assert failures == [failure]
        assert not (updates.root / "su_test" / "delivery.json").exists()
        runner._reconcile_resources()
        runner._reconcile_resources()
        fresh = SessionStore(store_fixture.root_path)
        index = fresh._open("p1")[1]
        results = [n for n in index.nodes_by_seq if n.metadata.get("source") == "self_update_result"]
        assert len(results) == 1
        assert index.head_id == "a1"
        receipt = updates._read_json(updates.root / "su_test" / "delivery.json", read_only=True)
        assert receipt["delivered"] == {"terminal": results[0].id}
    finally:
        runner.shutdown()


@pytest.mark.parametrize("missing", ["session", "origin"])
def test_missing_session_or_origin_remains_undelivered(store_fixture, tmp_path, monkeypatch, missing):
    from openprogram import paths
    from openprogram.agent.job.runner import JobRunner
    from openprogram.self_update import SelfUpdateStore, UpdatePhase

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    updates = SelfUpdateStore()
    updates.create(replace(_request(), session_id="p1",
                           origin_assistant_id="missing" if missing == "origin" else "a1"))
    updates.transition("su_test", UpdatePhase.ABORTED)
    if missing == "session":
        store_fixture.delete_session("p1")
    runner = JobRunner(max_workers=1)
    try:
        runner._reconcile_resources()
        assert not (updates.root / "su_test" / "delivery.json").exists()
        if missing == "session":
            assert store_fixture._open("p1") is None
            assert not store_fixture._session_dir("p1").exists()
        else:
            assert not any(n.metadata.get("source") == "self_update_result"
                           for n in store_fixture._open("p1")[1].nodes_by_seq)
        assert updates.load("su_test").state.phase is UpdatePhase.ABORTED
        assert runner.list_jobs("p1") == []
    finally:
        runner.shutdown()
