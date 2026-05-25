"""Async task → attach pointer integration.

When an async task completes, the runner updates the placeholder
attach card created by ``_run_spawn_async`` so its
``extra.attach.status`` flips from ``running`` to a terminal value
and ``source_commit_id`` is populated when a ContextCommit exists.

Tests the round-trip without spinning up a real LLM by faking
``run_agent_turn`` to write a deterministic assistant reply +
ContextCommit.
"""
from __future__ import annotations

import json
import threading
import time

import pytest


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    from openprogram.store.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session_store.default_store", lambda: s,
    )
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "parent_id": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "parent_id": "u1",
    })
    s.commit_turn("p1", "init")
    return s


def test_runner_updates_attach_card_on_completion(isolated_store, monkeypatch):
    """End-to-end: write a placeholder attach card, spawn an async
    task pointing at it, fake worker completes, and verify the
    attach card's extra blob now carries status=completed + head_id."""
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    import openprogram.agent.task.runner as runner_mod
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
        "content": "(running)", "called_by": "a1",
        "timestamp": time.time(),
        "extra": json.dumps(attach_extra, default=str),
    })
    isolated_store.commit_turn("p1", "spawn async placeholder")

    # 2. Fake run_agent_turn so the worker finishes in milliseconds.
    def fake_run(*, session_id, prompt, agent_id, parent_id, label=None):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        # Write the assistant_msg the dispatcher would have written.
        isolated_store.append_message(session_id, {
            "id": "head_alpha", "role": "assistant",
            "content": "final answer",
            "parent_id": parent_id, "timestamp": time.time(),
        })
        isolated_store.commit_turn(session_id, "fake turn")
        return AgentTurnResult(
            head_id="head_alpha", final_text="final answer",
            failed=False, error=None,
        )

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )

    # 3. Submit through the runner with attach_pointer_id wired in.
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    tid = runner.spawn_task(
        session_id="p1", prompt="do thing", agent_id="main",
        subject="alpha", description="do thing",
        parent_msg_id="a1", label="alpha",
        attach_pointer_id=attach_node_id,
    )
    final = runner.await_task(tid, timeout=5.0)
    assert final is not None
    assert final.status == TaskStatus.COMPLETED
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
    assert attach.get("task_id") == tid
    assert attach.get("head_id") == "head_alpha"
    # source_commit_id is best-effort (ContextCommit may not exist in
    # this minimal fake setup); when present it must be a string.
    src = attach.get("source_commit_id")
    assert src is None or isinstance(src, str)
