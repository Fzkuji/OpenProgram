"""Shared fixtures for async Job component tests."""
from __future__ import annotations

import threading

import pytest

from tests.support.waiting import wait_until


# Starting a job includes a durable jobs.json write and a session Git commit.
# Same-session commits are intentionally serialized; Windows filesystem and
# Defender latency can make a healthy pickup exceed the old one-second waits.
WORKER_START_TIMEOUT = 5.0


def _wait_for_stamped_cancel_reason(session_id: str) -> None:
    """Hold the fake turn until cancel has a stored reason.

    ``mark_cancelled`` trips the token before ``_cancel_single`` stamps
    ``reason_code``. Returning on the token alone lets the runner finalize
    as ``cancel.user`` even when the trigger was an idle/runtime budget.
    """
    from openprogram.agent.job.store import load_job
    from openprogram.agent.run_control import get_current_execution_id

    job_id = get_current_execution_id()
    if not job_id:
        return
    wait_until(
        lambda: (
            (job := load_job(session_id, job_id)) is not None
            and bool(job.reason_code)
        ),
        timeout=1.0,
    )


class _FakeMonotonic:
    def __init__(self) -> None:
        self._value = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds

@pytest.fixture
def store_fixture(tmp_path, monkeypatch):
    """Isolated SessionStore + session row for job tests."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: s,
    )
    monkeypatch.setattr(
        "openprogram.store.default_store", lambda: s,
    )
    monkeypatch.setattr(
        "openprogram.worker.lock.is_held_by", lambda _pid: True,
        raising=False,
    )
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
    return s

@pytest.fixture
def fake_worker(monkeypatch):
    """Replace run_agent_turn with a deterministic fake that records
    every invocation and respects the cancel event."""
    calls = []
    barrier = threading.Event()  # release worker when set
    cancel_seen = threading.Event()  # set inside fake when ev fires
    entered = threading.Event()  # set once the worker is INSIDE fake_run

    def fake_run(*, session_id, prompt, agent_id, branch_from=None, label=None, spawn_caller=None, advance_head=True):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        from openprogram.agent.run_control import is_cancelled
        calls.append({
            "session_id": session_id, "prompt": prompt,
            "agent_id": agent_id, "branch_from": branch_from, "label": label,
        })
        # Signal "worker is past the pending→running transition and
        # actually executing fake_run". Tests that want to cancel
        # mid-run wait on this before calling cancel_job — otherwise
        # the runner can flip pending→cancelled before the worker
        # picks up the future and the worker body never runs.
        entered.set()
        # Hold until the test releases the barrier or this job is
        # cancelled with a stamped reason. Returning on the token
        # alone races the budget/user stamp and finalizes as cancel.user.
        while not barrier.is_set():
            if is_cancelled(session_id):
                _wait_for_stamped_cancel_reason(session_id)
                cancel_seen.set()
                return AgentTurnResult(head_id="head_x", final_text="",
                                       failed=True, error="cancelled")
            barrier.wait(0.02)
        return AgentTurnResult(head_id="head_ok", final_text="hello",
                               failed=False, error=None)

    import openprogram.agent.job.runner as runner_mod
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_run,
    )
    yield calls, barrier, cancel_seen, entered
    # Cleanup any singleton runner so the next test gets a fresh pool.
    runner_mod.shutdown_runner()
