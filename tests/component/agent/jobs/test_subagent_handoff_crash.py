"""A Worker killed at each hand-off between a parent and its sub-agent.

Each case runs two real processes on one state directory. The first drives
the production path (the ``agent`` tool, the job runner, the inbox) and is
killed with ``os._exit`` at the named step, so no ``finally`` block runs. The
second starts a fresh job runner the way the next Worker does, lets its
startup recovery run, and reports what it found. Only the sub-agent's model
turn is faked; it records every run so a request that runs twice shows.

Windows, in the order a request crosses them:

  * ``send_message`` to a busy target: admitted, then queued in the inbox;
  * the inbox drain: published to the dispatcher, then the entry removed;
  * a spawn: admitted held, its attach card written, then published;
  * the end: the terminal write, then the parent's notice.
"""
from __future__ import annotations

import multiprocessing
import os
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from tests.component.runtime.test_always_on_crash_matrix import _KILLED, _die_after, _die_before, _kill
from tests.support.waiting import wait_until

_NEVER_KILLED_S = 20


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two conversations on disk, a parent turn bound on p1:a1, a fake model."""
    from openprogram.runtime.execution.run_control import binding as run_control_binding
    from openprogram.state import store as store_mod
    from openprogram.state.store.session.session_store import SessionStore
    import openprogram.state.paths as paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    root = tmp_path / "sessions-git"
    runs = tmp_path / "runs.log"
    store = SessionStore(root)
    store.create_session("p1", "main", title="parent")
    for message in (
        {"id": "u1", "role": "user", "content": "hi", "timestamp": 0, "predecessor": None},
        {"id": "a1", "role": "assistant", "content": "ok", "timestamp": 0, "predecessor": "u1"},
    ):
        store.append_message("p1", message)
    store.commit_turn("p1", "init")
    store.create_session("p2", "main", title="other")
    for message in (
        {"id": "u9", "role": "user", "content": "x", "timestamp": 0, "predecessor": None},
        {"id": "a9", "role": "assistant", "content": "y", "timestamp": 0, "predecessor": "u9"},
    ):
        store.append_message("p2", message)
    store.commit_turn("p2", "init")
    store.close()

    def model_turn(*, request, cancel_event, **_kwargs):
        from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult

        with runs.open("a") as handle:
            handle.write(f"{request.session_id}\n")
        return AgentTurnResult(head_id="head_ok", final_text="found it", failed=False, error=None)

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(model_turn),
    )
    sid_token = run_control_binding._current_session_id.set("p1")
    turn_token = store_mod._current_turn_id.set("a1")
    try:
        yield SimpleNamespace(root=root, runs=runs)
    finally:
        run_control_binding._current_session_id.reset(sid_token)
        store_mod._current_turn_id.reset(turn_token)


def _open(world, *, busy: bool) -> Any:
    """This process's own stores, as a new Worker opens them."""
    import openprogram.runtime.conversation.run_control as run_control
    from openprogram.runtime.context import session_db
    from openprogram.runtime.jobs import notices
    from openprogram.runtime.jobs.runner import shared
    from openprogram.state import store as store_pkg
    from openprogram.state.store.session import session_store
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(world.root)
    session_db.default_store = lambda: store
    session_store.default_store = lambda: store
    store_pkg.default_store = lambda: store
    shared._broadcast = lambda *a, **k: None
    # A new Worker has no runner yet. A forked child inherits the parent's
    # runner object, if an earlier test on this pytest worker left one, but
    # none of its threads, so it would never dispatch.
    shared._runner, shared._runner_lock = None, threading.Lock()
    notices._due, notices._seeded = set(), True
    run_control.is_turn_running = lambda sid: busy and sid == "p2"
    return store


def _in_a_process(body: Callable[[], Any]) -> SimpleNamespace:
    """Run ``body`` in its own process. Returns its exit code and answer."""
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)

    def run() -> None:
        from openprogram.operations.worker.lock import WorkerLock

        try:
            lock = WorkerLock()  # held, like a Worker's, until the process ends
            assert lock.try_acquire(), "the previous Worker still holds the lock"
            send.send({"answer": body()})
        except BaseException:  # noqa: BLE001 - the parent shows this process's failure
            send.send({"error": traceback.format_exc()})
        finally:
            send.close()
        os._exit(0)

    process = context.Process(target=run)
    process.start()
    send.close()
    process.join(90)
    if process.is_alive():
        process.kill()
        process.join(10)
        raise AssertionError("the process never finished")
    try:
        message = receive.recv() if receive.poll(0) else {}
    except EOFError:  # killed before it could answer
        message = {}
    assert "error" not in message, message.get("error")
    return SimpleNamespace(exit_code=process.exitcode, answer=message.get("answer"))


def _dies(world, act: Callable[[], Any], *, busy: bool = True) -> None:
    def body() -> None:
        _open(world, busy=busy)
        act()
        import time
        time.sleep(_NEVER_KILLED_S)

    assert _in_a_process(body).exit_code == _KILLED


def _next_worker(world, session_id: str, *, busy: bool = False,
                 then: Callable[[Any], None] | None = None) -> dict[str, Any]:
    """Start a runner (its recovery runs), let the work finish, report the state."""
    def body() -> dict[str, Any]:
        from openprogram.runtime.conversation import inbox
        from openprogram.runtime.jobs import get_runner, notices
        from openprogram.runtime.jobs.types import is_terminal

        store = _open(world, busy=busy)
        runner = get_runner()
        jobs = runner.list_jobs(session_id)
        found = {
            "after_recovery": [(job.status.value, job.error) for job in jobs],
            "inbox_after_recovery": [entry.get("job_id") for entry in inbox._load(inbox._inbox_path(session_id))],
        }
        if then is not None:
            then(runner)
        if jobs:
            # Finished, and its parent's notice settled (or not owed).
            wait_until(lambda: all(is_terminal(job.status) and not job.notice_pending
                                   for job in runner.list_jobs(session_id)), timeout=30)
        runner._reconcile_resources()  # a second pass changes nothing
        jobs = runner.list_jobs(session_id)
        notice_path = notices._path("p1")
        card_ids = [job.attach_pointer_id for job in jobs]
        nodes = store._open("p1")[1].all_nodes()  # noqa: SLF001 - every row, runtime ones too
        return {
            **found,
            "jobs": [(job.id, job.status.value, job.error, job.notice_pending) for job in jobs],
            "inbox": [entry.get("job_id") for entry in inbox._load(inbox._inbox_path(session_id))],
            "notices": [entry.get("job_id") for entry in notices._load(notice_path)[0]] if notice_path.exists() else [],
            "notice_kinds": [item["kind"] for item in notices.notice_items(jobs)],
            "cards": [node.id for node in nodes if node.id in card_ids],
            "card_ids": card_ids,
            "runs": len(world.runs.read_text().splitlines()) if world.runs.exists() else 0,
        }

    return _in_a_process(body).answer


def _dispatch_to_busy() -> None:
    from openprogram.programs.tools.agents.send_message.send_message.send_message import (
        _send_message_impl,
    )
    from openprogram.runtime.jobs import get_runner

    get_runner()
    result = _send_message_impl("audit the config", to="p2:a9")
    assert result.startswith("[queued]"), result


def _drain(runner) -> None:
    from openprogram.runtime.conversation import inbox
    inbox.drain("p2")


# ── send_message to a busy target: admission → inbox ──────────────────


def test_killed_after_admission_before_the_inbox_the_dispatch_still_runs(world):
    def act() -> None:
        from openprogram.runtime.conversation import inbox
        _die_before(inbox, "enqueue")
        _dispatch_to_busy()

    _dies(world, act)
    found = _next_worker(world, "p2", then=_drain)
    assert found["after_recovery"] == [("queued", None)]
    assert len(found["inbox_after_recovery"]) == 1
    assert [row[1] for row in found["jobs"]] == ["completed"] and found["inbox"] == []
    assert found["runs"] == 1


def test_killed_right_after_the_inbox_write_the_entry_stays_once(world):
    def act() -> None:
        from openprogram.runtime.conversation import inbox
        _die_after(inbox, "enqueue")
        _dispatch_to_busy()

    _dies(world, act)
    found = _next_worker(world, "p2", then=_drain)
    # A restart neither errors the waiting dispatch nor queues it twice.
    assert found["after_recovery"] == [("queued", None)]
    assert len(found["inbox_after_recovery"]) == 1
    assert [row[1] for row in found["jobs"]] == ["completed"] and found["runs"] == 1


# ── the inbox drain: publish → entry removed ─────────────────────────


def test_killed_after_the_drain_published_the_job_it_runs_once(world):
    def act() -> None:
        import openprogram.runtime.conversation.run_control as run_control
        from openprogram.runtime.conversation import inbox
        from openprogram.runtime.jobs import get_runner

        _dispatch_to_busy()
        run_control.is_turn_running = lambda sid: False
        _die_after(get_runner()._governor, "mark_dispatch_ready")
        inbox.drain("p2")

    _dies(world, act)
    found = _next_worker(world, "p2", then=_drain)
    assert len(found["inbox_after_recovery"]) == 1  # left behind by the crash
    assert [row[1] for row in found["jobs"]] == ["completed"]
    assert found["inbox"] == [] and found["runs"] == 1


# ── a spawn: admission → attach card → publish ───────────────────────


def _spawn_in_background() -> None:
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    from openprogram.runtime.jobs import get_runner

    get_runner()
    _agent_impl("read the worker log", description="scout", run_in_background=True)


def test_background_spawn_killed_before_its_card_still_runs_and_reports(world):
    def act() -> None:
        from openprogram.runtime.conversation import sub_agent_run
        _die_before(sub_agent_run, "write_attach_placeholder_for_spawn")
        _spawn_in_background()

    _dies(world, act, busy=False)
    found = _next_worker(world, "p1")
    assert [row[1] for row in found["jobs"]] == ["completed"] and found["runs"] == 1
    assert found["cards"] == found["card_ids"]  # written once, by recovery
    assert found["notices"] == [found["jobs"][0][0]] and found["jobs"][0][3] is None


def test_background_spawn_killed_after_its_card_is_published_without_a_second_card(world):
    def act() -> None:
        from openprogram.runtime.jobs.resource_governance import ResourceGovernor
        _die_before(ResourceGovernor, "publish_accepted_job")
        _spawn_in_background()

    _dies(world, act, busy=False)
    found = _next_worker(world, "p1")
    assert [row[1] for row in found["jobs"]] == ["completed"] and found["runs"] == 1
    assert found["cards"] == found["card_ids"]
    assert found["notices"] == [found["jobs"][0][0]]


def test_waited_spawn_whose_waiter_died_reports_by_notice(world):
    def act() -> None:
        from openprogram.runtime.conversation.sub_agent_run import write_attach_placeholder_for_spawn
        from openprogram.runtime.jobs import get_runner
        from openprogram.runtime.jobs.resource_governance import ResourceGovernor

        _die_before(ResourceGovernor, "publish_accepted_job")
        get_runner().spawn_job(
            session_id="p1", prompt="read the worker log", agent_id="main",
            parent_msg_id="a1", caller_msg_id="a1", label="scout", wait=True,
            attach_pointer_id="card_sync",
            on_accepted=lambda job: write_attach_placeholder_for_spawn(
                session_id="p1", caller_msg_id="a1", label="scout",
                prompt="read the worker log", chosen_agent="main",
                node_id="card_sync", job_id=job.id, delivery="tool_result",
            ),
        )

    _dies(world, act, busy=False)
    found = _next_worker(world, "p1")
    assert [row[1] for row in found["jobs"]] == ["completed"] and found["runs"] == 1
    assert found["cards"] == ["card_sync"]
    # Nobody is left to take the tool result, so the parent hears it, inline.
    assert found["notices"] == [found["jobs"][0][0]] and found["notice_kinds"] == ["reply"]


# ── the end: terminal write → the parent's notice ────────────────────


def test_killed_between_the_terminal_write_and_the_notice_the_parent_still_hears(world):
    def act() -> None:
        from openprogram.runtime.jobs import notices
        _die_before(notices, "queue")
        _spawn_in_background()

    _dies(world, act, busy=False)
    found = _next_worker(world, "p1")
    assert [row[1] for row in found["jobs"]] == ["completed"] and found["runs"] == 1
    assert found["notices"] == [found["jobs"][0][0]] and found["jobs"][0][3] is None


def test_a_waited_spawn_answered_in_time_sends_no_notice(world):
    """The ordinary case: the waiter is alive, so the result went back as the tool result."""
    def body() -> dict[str, Any]:
        from openprogram.runtime.conversation.sub_agent_run import run_agent_turn
        from openprogram.runtime.jobs import get_runner, notices

        _open(world, busy=False)
        get_runner()
        result = run_agent_turn("p1", "read the worker log", "main", branch_from="a1")
        path = notices._path("p1")
        return {"text": result.final_text,
                "notices": notices._load(path)[0] if path.exists() else [],
                "pending": [job.notice_pending for job in get_runner().list_jobs("p1")]}

    found = _in_a_process(body).answer
    assert found == {"text": "found it", "notices": [], "pending": [None]}


# ── what the recovered card and message keep ────────────────────────


def _card_tool_call_ids(world) -> list:
    def body() -> list:
        import json
        store = _open(world, busy=False)
        out = []
        for node in store._open("p1")[1].all_nodes():  # noqa: SLF001 - runtime rows too
            extra = node.metadata or {}
            attach = extra.get("attach") if isinstance(extra.get("attach"), dict) else None
            if attach is None and isinstance(extra.get("extra"), str):
                attach = json.loads(extra["extra"]).get("attach")
            if isinstance(attach, dict):
                out.append(attach.get("tool_call_id"))
        return out
    return _in_a_process(body).answer


def test_a_card_written_by_recovery_keeps_the_calls_tool_call_id(world):
    def act() -> None:
        from openprogram.programs._host import runtime as host_runtime
        from openprogram.runtime.conversation import sub_agent_run
        _die_before(sub_agent_run, "write_attach_placeholder_for_spawn")
        host_runtime._current_tool_call_id.set("call_abc")
        _spawn_in_background()

    _dies(world, act, busy=False)
    found = _next_worker(world, "p1")
    assert [row[1] for row in found["jobs"]] == ["completed"] and found["runs"] == 1
    # The recovered card still answers the agent call that started it.
    assert _card_tool_call_ids(world) == ["call_abc"]


def test_a_message_to_an_agent_by_name_survives_a_kill_after_the_inbox_write(world):
    def act() -> None:
        from openprogram.programs.tools.agents.send_message.send_message.send_message import (
            _send_message_impl,
        )
        from openprogram.runtime.agent_registry import manager
        from openprogram.runtime.conversation import inbox
        from openprogram.runtime.jobs import get_runner

        manager.list_all = lambda: []
        get_runner()
        _die_after(inbox, "enqueue")
        # By the conversation's title, the way an Agent's name finds its conversation.
        result = _send_message_impl("audit the config", to="OTHER")
        assert result.startswith("[queued]"), result

    _dies(world, act)
    found = _next_worker(world, "p2", then=_drain)
    assert found["after_recovery"] == [("queued", None)]
    assert [row[1] for row in found["jobs"]] == ["completed"] and found["runs"] == 1
