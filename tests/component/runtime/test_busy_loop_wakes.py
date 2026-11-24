"""A notice held for a busy conversation goes out when the conversation goes
quiet, not at the next look of a short timer."""
from __future__ import annotations

import asyncio
import time

import pytest


async def _run_until(loop_fn, stop: asyncio.Event, predicate, timeout: float) -> bool:
    task = asyncio.create_task(loop_fn(stop))
    try:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return True
            await asyncio.sleep(0.02)
        return predicate()
    finally:
        stop.set()
        await asyncio.wait_for(task, 5)


def test_agent_notices_wait_for_a_turn_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    from openprogram.runtime.execution.run_control.tokens import turn_ended
    from openprogram.runtime.jobs import notices

    sweeps: list[float] = []
    monkeypatch.setattr(notices, "_sweep_all", lambda: sweeps.append(time.monotonic()) or len(sweeps) < 2)
    monkeypatch.setattr(notices, "RETRY_SECONDS", 60.0)

    async def scenario() -> bool:
        stop = asyncio.Event()

        async def end_a_turn_later() -> None:
            await asyncio.sleep(0.3)
            assert len(sweeps) == 1, "nothing looks while the conversation is busy"
            turn_ended("busy-session")

        asyncio.get_running_loop().create_task(end_a_turn_later())
        return await _run_until(notices.run_job_notices, stop, lambda: len(sweeps) >= 2, 3.0)

    assert asyncio.run(scenario())


def test_an_idle_signal_does_not_wake_agent_notices_with_nothing_waiting(monkeypatch) -> None:
    from openprogram.runtime.execution.run_control.tokens import turn_ended
    from openprogram.runtime.jobs import notices

    sweeps: list[float] = []
    monkeypatch.setattr(notices, "_sweep_all", lambda: sweeps.append(time.monotonic()) or False)

    async def scenario() -> int:
        stop = asyncio.Event()
        task = asyncio.create_task(notices.run_job_notices(stop))
        await asyncio.sleep(0.2)
        turn_ended("quiet-session")
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.wait_for(task, 5)
        return len(sweeps)

    assert asyncio.run(scenario()) == 1


def test_a_command_exit_notice_waits_for_a_turn_to_end(monkeypatch, tmp_path) -> None:
    from openprogram.host._platform import changes
    from openprogram.runtime.conversation import process_notices
    from openprogram.runtime.execution.run_control.tokens import turn_ended

    class _Store:
        path = tmp_path / "processes.db"

    sweeps: list[float] = []
    monkeypatch.setattr(process_notices, "ProcessStore", _Store)
    monkeypatch.setattr(changes, "watch", lambda *a, **k: None)
    monkeypatch.setattr(changes, "file_events_available", lambda: True)
    monkeypatch.setattr(process_notices, "sweep",
                        lambda store: sweeps.append(time.monotonic()) or len(sweeps) < 2)

    async def scenario() -> bool:
        stop = asyncio.Event()

        async def end_a_turn_later() -> None:
            await asyncio.sleep(0.3)
            assert len(sweeps) == 1, "nothing looks while the conversation is busy"
            turn_ended("busy-session")

        asyncio.get_running_loop().create_task(end_a_turn_later())
        return await _run_until(process_notices.run_exit_notices, stop, lambda: len(sweeps) >= 2, 3.0)

    assert asyncio.run(scenario())


def test_an_admitted_turn_takes_its_slot_when_the_turn_before_it_ends(monkeypatch) -> None:
    import threading

    from openprogram.runtime.conversation.run_control import run_slot
    from openprogram.runtime.execution.run_control.tokens import turn_ended

    free = threading.Event()
    asked: list[float] = []

    class Port:
        def adopt(self, *_a, **_k) -> bool:
            asked.append(time.monotonic())
            return free.is_set()

    monkeypatch.setattr(run_slot, "_port", Port())

    def end_the_turn_before() -> None:
        time.sleep(0.3)
        free.set()
        turn_ended("goal-session")

    threading.Thread(target=end_the_turn_before, daemon=True).start()
    started = time.monotonic()
    assert run_slot.hold_admitted(
        "goal-session", "goal_user", execution_id="e", status_version=1, branch_id=None,
        runner=threading.current_thread(), still_wanted=lambda: True, poll_s=60.0)
    assert time.monotonic() - started < 5.0
    # Asked once before the turn ended and once when it did; nothing in between.
    assert len(asked) == 2


def test_a_slot_given_back_says_so_even_when_the_next_turn_shows_as_running(monkeypatch) -> None:
    """A Goal's next turn is admitted before the turn before it ends; once
    that turn gives the slot back, the frame reports the waiting turn (its
    queued record) as running, and the waiter still has to hear it."""
    from openprogram.host._platform import changes
    from openprogram.interfaces.server import server as state
    from openprogram.interfaces.server.server_runtime import session_lifecycle, session_persistence
    from openprogram.runtime.execution.run_control.tokens import TURN_ENDED_TOPIC

    frames = []
    monkeypatch.setattr(state, "_broadcast", frames.append)
    monkeypatch.setattr(session_lifecycle, "_reported_run_task",
                        lambda _sid, _task: {"msg_id": "next_user", "execution_id": "next"})
    listener = changes.listen(TURN_ENDED_TOPIC)
    try:
        session_persistence._emit_running_task_event("s1", cleared_msg_id="previous_user",
                                                     cleared_execution_id="previous")
        assert '"running_task"' in frames[-1]
        assert f"{TURN_ENDED_TOPIC}:s1" in listener.wait(0)
        session_persistence._emit_running_task_event("s1")
        assert not listener.wait(0)
    finally:
        listener.close()


def test_a_runs_live_tree_goes_out_when_its_nodes_are_written(monkeypatch) -> None:
    import threading
    from types import SimpleNamespace

    from openprogram.host._platform import changes
    from openprogram.runtime.execution.projections import dag
    from openprogram.state.store.session.files.session_log import RECORDS_TOPIC
    from openprogram.state.store.session.graph import branches

    tree = {"path": "fn", "name": "f", "status": "running", "children": []}
    builds: list[float] = []

    def build(*_args):
        builds.append(time.monotonic())
        return json_copy(tree)

    monkeypatch.setattr(dag, "build_exec_dag", build)
    monkeypatch.setattr(branches, "build_branches_payload", lambda *_a: {})
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db",
                        lambda: SimpleNamespace(invalidate_cache=lambda *_: None))
    monkeypatch.setattr(dag, "LIVE_MIN_GAP_SECONDS", 0.05)
    frames: list[dict] = []
    first = threading.Event()
    second = threading.Event()

    def sink(frame) -> None:
        if frame.get("type") == "chat_response":
            frames.append(frame)
            (second if first.is_set() else first).set()

    with dag.live_progress("live-session", "reply", "f", on_event=sink):
        assert first.wait(10)
        time.sleep(0.4)
        assert len(builds) == 1, "nothing is read while nothing is written"
        # Another session's write does not wake this run.
        changes.signal(RECORDS_TOPIC, "other-session")
        time.sleep(0.2)
        assert len(builds) == 1
        tree["children"] = [{"path": "step", "name": "s", "status": "running"}]
        changes.signal(RECORDS_TOPIC, "live-session")
        assert second.wait(5)
    assert frames[1]["data"]["tree"]["children"][0]["path"] == "step"


def json_copy(value):
    import json
    return json.loads(json.dumps(value))
