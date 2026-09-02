"""Async job → attach pointer component coverage.

When an async job completes, the runner updates the placeholder
attach card created by ``_run_spawn_async`` so its
``extra.attach.status`` flips from ``running`` to a terminal value
and ``source_commit_id`` is populated when a ContextCommit exists.

Tests the round-trip without spinning up a real LLM by faking the canonical
Agent turn runner to write a deterministic assistant reply +
ContextCommit.
"""
from __future__ import annotations

import atexit
import json
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def runner_lifecycle(monkeypatch):
    """Run the real local pool under the worker lock and always close it."""
    import openprogram.agent.job.runner as runner_mod

    runner_mod.shutdown_runner()
    monkeypatch.setattr("openprogram.worker.lock.is_held_by", lambda _pid: True)
    yield
    runner_mod.shutdown_runner()


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: s,
    )
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "predecessor": "u1",
    })
    s.commit_turn("p1", "init")
    try:
        yield s
    finally:
        timer = s._index_timer
        s._flush_index()
        if timer is not None:
            timer.join(timeout=1)
        atexit.unregister(s._flush_index)


def test_runner_updates_attach_card_on_completion(isolated_store, monkeypatch):
    """End-to-end: write a placeholder attach card, spawn an async
    job pointing at it, fake worker completes, and verify the
    attach card's extra blob now carries status=completed + head_id."""
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()

    # 1. Write placeholder attach card (mirrors _run_spawn_async).
    attach_node_id = "atc_zero"
    attach_extra = {
        "attach": {
            "session_id": "p1", "head_id": None,
            "label": "alpha", "prompt": "do thing",
            "source_commit_id": None, "status": "running",
        }
    }
    isolated_store.append_message("p1", {
        "id": attach_node_id, "role": "assistant",
        "display": "runtime", "function": "attach",
        "content": "(running)", "predecessor": "a1",
        "timestamp": time.time(),
        "extra": json.dumps(attach_extra, default=str),
    })
    isolated_store.commit_turn("p1", "spawn async placeholder")

    # 2. Fake the canonical Agent turn runner so the worker finishes in milliseconds.
    def fake_run(*, request, cancel_event, **_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        # Write the assistant_msg the dispatcher would have written.
        isolated_store.append_message(request.session_id, {
            "id": "head_alpha", "role": "assistant",
            "content": "final answer",
            "predecessor": request.branch_from, "timestamp": time.time(),
        })
        isolated_store.commit_turn(request.session_id, "fake turn")
        return AgentTurnResult(
            head_id="head_alpha", final_text="final answer",
            failed=False, error=None,
        )

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_run),
    )

    # 3. Submit through the runner with attach_pointer_id wired in.
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    tid = runner.spawn_job(
        session_id="p1", prompt="do thing", agent_id="main",
        subject="alpha", description="do thing",
        parent_msg_id="a1", label="alpha",
        attach_pointer_id=attach_node_id,
    )
    final = runner.await_job(tid, timeout=5.0)
    assert final is not None
    assert final.status == JobStatus.COMPLETED
    assert final.head_id == "head_alpha"

    # 4. Inspect the attach card — extra should now reflect terminal.
    pair = isolated_store._open("p1")
    assert pair is not None
    _, idx = pair
    node = idx.nodes_by_id.get(attach_node_id)
    assert node is not None
    md = node.metadata or {}
    extra_raw = md.get("extra")
    extra = json.loads(extra_raw) if isinstance(extra_raw, str) else (extra_raw or {})
    attach = extra.get("attach") or {}
    assert attach.get("status") == "completed"
    assert attach.get("job_id") == tid
    assert attach.get("head_id") == "head_alpha"
    # source_commit_id is best-effort (ContextCommit may not exist in
    # this minimal fake setup); when present it must be a string.
    src = attach.get("source_commit_id")
    assert src is None or isinstance(src, str)


def test_attach_card_carries_the_subagent_name(isolated_store, monkeypatch):
    """The sub-agent's human name lands on the attach node.

    It is the only row both the DAG wire and the transcript read, so
    without it the branch has no identity in either view — the case that
    motivated this showed "1daf47f4" where "后端架构" belonged. Falls back
    to ``subject`` when the caller passed no explicit label.
    """
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()

    attach_node_id = "atc_named"
    isolated_store.append_message("p1", {
        "id": attach_node_id, "role": "assistant",
        "display": "runtime", "function": "attach",
        "content": "(running)", "predecessor": "a1",
        "timestamp": time.time(),
        "extra": json.dumps({"attach": {"status": "running"}}, default=str),
    })
    isolated_store.commit_turn("p1", "spawn async placeholder")

    def fake_run(*, request, cancel_event, **_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        isolated_store.append_message(request.session_id, {
            "id": "head_named", "role": "assistant", "content": "done",
            "predecessor": request.branch_from, "timestamp": time.time(),
        })
        isolated_store.commit_turn(request.session_id, "fake turn")
        return AgentTurnResult(head_id="head_named", final_text="done")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_run),
    )

    from openprogram.agent.job import get_runner
    runner = get_runner()
    # No ``label`` — only a subject, which is where the case data's name
    # actually lived.
    tid = runner.spawn_job(
        session_id="p1", prompt="read the backend", agent_id="main",
        subject="后端架构", description="read the backend",
        parent_msg_id="a1", attach_pointer_id=attach_node_id,
    )
    assert runner.await_job(tid, timeout=5.0) is not None

    _, idx = isolated_store._open("p1")
    md = (idx.nodes_by_id[attach_node_id].metadata or {})
    assert (md.get("attach") or {}).get("label") == "后端架构"
    extra = json.loads(md["extra"])
    assert extra["attach"]["label"] == "后端架构"


def test_attach_write_broadcasts_reload_and_context_stats(
    isolated_store, monkeypatch,
):
    """Finishing a sub-agent tells both readers of the graph.

    The DAG re-pulls the session and the context ring re-estimates —
    without this the graph on screen still showed the pre-spawn shape
    until the user clicked something.
    """
    frames: list[dict] = []
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", frames.append,
    )
    stats: list[str] = []
    monkeypatch.setattr(
        "openprogram.agent.job.runner._refresh_context_stats", stats.append,
    )
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()

    attach_node_id = "atc_cast"
    isolated_store.append_message("p1", {
        "id": attach_node_id, "role": "assistant",
        "display": "runtime", "function": "attach",
        "content": "(running)", "predecessor": "a1",
        "timestamp": time.time(),
        "extra": json.dumps({"attach": {"status": "running"}}, default=str),
    })
    isolated_store.commit_turn("p1", "spawn async placeholder")

    from openprogram.agent.job.types import Job, JobStatus
    runner_mod.get_runner()._update_attach_card(Job(
        id="t_cast", parent_session_id="p1", prompt="x", agent_id="main",
        status=JobStatus.COMPLETED, head_id="head_alpha",
        result_text="done", label="前端测试",
        attach_pointer_id=attach_node_id,
    ))

    reloads = [
        f for f in frames
        if f.get("type") == "session_reload"
        and (f.get("data") or {}).get("session_id") == "p1"
    ]
    assert reloads, "attach write must broadcast a session reload"
    assert stats == ["p1"]
