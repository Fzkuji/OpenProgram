"""Follow-up notifications anchor at HEAD and advance it, in series.

Two sub-agents finishing used to produce two notification turns both
anchored at the node that spawned them — parallel siblings, so one user
message got answered twice. The runner now leaves ``branch_from`` at
``INHERIT_PARENT`` (the dispatcher resolves it to the session's current
HEAD) and serialises follow-ups per delivery session.

See docs/reference/design/runtime/dag/overview.md §4.
"""
from __future__ import annotations

import threading

import pytest

from openprogram.agent.dispatcher.types import INHERIT_PARENT
from openprogram.agent.job.types import Job, JobStatus


@pytest.fixture(autouse=True)
def _runner_lifecycle(monkeypatch):
    """Keep the singleton pool and its control threads inside each test."""
    from openprogram.agent.job import runner as runner_mod

    runner_mod.shutdown_runner()
    monkeypatch.setattr("openprogram.worker.lock.is_held_by", lambda _pid: True)
    yield
    runner_mod.shutdown_runner()


def _make_job(**kw):
    base = dict(
        id="t_x",
        parent_session_id="S",
        prompt="do thing",
        agent_id="main",
        status=JobStatus.COMPLETED,
        head_id="sub_tip",
        result_text="sub answer",
        caller_msg_id="spawning_reply",
    )
    base.update(kw)
    return Job(**base)


def test_followup_anchors_at_head_not_the_spawning_node(monkeypatch):
    """``branch_from`` stays INHERIT_PARENT — the dispatcher walks HEAD."""
    from openprogram.agent.job import runner as runner_mod

    seen = {}
    done = threading.Event()

    def fake_process(req, **kw):
        seen["branch_from"] = req.branch_from
        seen["session_id"] = req.session_id
        done.set()
        return type("_R", (), {})()

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_process)
    runner_mod.get_runner()._dispatch_followup(_make_job())

    assert done.wait(2)
    assert seen["session_id"] == "S"
    # Not pinned to caller_msg_id — that pin is what forked the branch.
    assert seen["branch_from"] is INHERIT_PARENT
    assert seen["branch_from"] != "spawning_reply"


def test_followup_never_resets_head_backwards(monkeypatch):
    """The old code rewound HEAD to the spawning node before the turn.

    That rewind is precisely what made the second follow-up land beside
    the first instead of after it, so the runner must not call set_head.
    """
    from openprogram.agent.job import runner as runner_mod

    calls = []
    done = threading.Event()

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(
        disp,
        "process_user_turn",
        lambda req, **kw: (done.set(), type("_R", (), {})())[1],
    )
    from openprogram.agent import session_db as sdb
    monkeypatch.setattr(sdb, "default_db", lambda: type("S", (), {
        "set_head": staticmethod(lambda *a, **k: calls.append(a)),
    })())
    runner_mod.get_runner()._dispatch_followup(_make_job())

    assert done.wait(2)
    assert calls == []


def test_two_followups_form_a_serial_chain(monkeypatch):
    """Two sub-agents finishing → notice₁ → answer₁ → notice₂ → answer₂.

    The fake dispatcher models the real one: it appends the notification
    at the current HEAD and moves HEAD to the answer. A parallel-branch
    regression shows up as two nodes sharing a predecessor.
    """
    from openprogram.agent.job import runner as runner_mod

    # id → predecessor, in write order.
    graph: list[tuple[str, str | None]] = [("spawning_reply", "user_msg")]
    head = {"id": "spawning_reply"}
    n = {"i": 0}
    done = threading.Event()

    def fake_process(req, **kw):
        n["i"] += 1
        anchor = (
            head["id"] if req.branch_from is INHERIT_PARENT else req.branch_from
        )
        notice = f"notice{n['i']}"
        answer = f"answer{n['i']}"
        graph.append((notice, anchor))
        graph.append((answer, notice))
        head["id"] = answer
        if n["i"] == 2:
            done.set()
        return type("_R", (), {})()

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_process)
    r = runner_mod.get_runner()
    r._dispatch_followup(_make_job(id="t_1", label="后端架构"))
    r._dispatch_followup(_make_job(id="t_2", label="前端测试"))

    assert done.wait(2)
    preds = dict(graph)
    # Serial: the second notification hangs off the first answer.
    assert preds["notice1"] == "spawning_reply"
    assert preds["answer1"] == "notice1"
    assert preds["notice2"] == "answer1"
    assert preds["answer2"] == "notice2"
    # No two nodes share a predecessor → no sibling branches at all.
    parents = [p for _, p in graph if p]
    assert len(parents) == len(set(parents))


def test_concurrent_followups_are_serialised(monkeypatch):
    """Two follow-ups dispatched from different threads still interleave
    cleanly: the per-session lock means one turn completes before the
    next reads HEAD."""
    from openprogram.agent.job import runner as runner_mod

    order: list[str] = []
    in_turn = threading.Lock()

    def fake_process(req, **kw):
        # Fails loudly if two turns are ever in flight at once.
        assert in_turn.acquire(blocking=False), "concurrent follow-up turns"
        try:
            order.append("enter")
            threading.Event().wait(0.02)
            order.append("exit")
        finally:
            in_turn.release()
        return type("_R", (), {})()

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_process)

    r = runner_mod.get_runner()
    threads = [
        threading.Thread(target=r._dispatch_followup, args=(_make_job(id=f"t_{i}"),))
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    # The daemon threads the runner spawns need a moment to drain.
    for _ in range(200):
        if order.count("exit") == 2:
            break
        threading.Event().wait(0.02)

    assert order == ["enter", "exit", "enter", "exit"]


def test_followup_lock_is_per_delivery_session(monkeypatch):
    """Different sessions get different locks — one slow session must not
    hold up another's notification."""
    from openprogram.agent.job import runner as runner_mod

    r = runner_mod.get_runner()
    a = r._followup_lock("sess_a")
    b = r._followup_lock("sess_b")
    assert a is not b
    assert r._followup_lock("sess_a") is a
