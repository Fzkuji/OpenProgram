"""A turn the runtime starts on its own takes the conversation's run slot."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.support.thread_scope import fake_threads_started_by


@pytest.fixture
def owner_turn_env(monkeypatch):
    import openprogram.runtime.agent.turn_request as agent_turn_request
    import openprogram.runtime.conversation.turn_admission.owner_turn as owner_turn
    from openprogram.interfaces.server import server as _s

    admitted: list[str] = []
    failed: list[str] = []
    started: list[object] = []

    class Adapter:
        def __init__(self, **_kw):
            pass

        def admit(self, request, **_kw):
            admitted.append(request.session_id)
            return SimpleNamespace(execution_id="e-owner", status_version=1)

        def fail_admission(self, admission, *, reason_code):
            failed.append(reason_code)

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter", Adapter)
    monkeypatch.setattr(
        "openprogram.runtime.conversation.session_settings.session_config.load_session_run_config",
        lambda sid: SimpleNamespace(permission_mode=None, thinking_effort=None,
                                    additional_working_dirs=[]))
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda sid: None)
    monkeypatch.setattr("openprogram.runtime.permissions.authority.local_owner_authority", lambda: {})
    monkeypatch.setattr(agent_turn_request, "TurnRequest", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(_s, "_emit_running_task_event", lambda *a, **kw: None)

    class Thread:
        def __init__(self, *, target, **_kw):
            self.target = target

        def start(self):
            started.append(self)

    fake_threads_started_by(monkeypatch, owner_turn, Thread)
    return SimpleNamespace(
        owner_turn=owner_turn, server=_s, admitted=admitted, failed=failed, started=started,
    )


def _clear(server, session_id):
    with server._running_tasks_lock:
        server._running_tasks.pop(session_id, None)
    server._unregister_active_runtime(session_id)


def test_owner_turn_is_refused_while_a_chat_turn_holds_the_slot(owner_turn_env):
    from openprogram.runtime.conversation.run_control.run_slot import RunSlotBusy

    env = owner_turn_env
    session_id = "owner-busy"
    _clear(env.server, session_id)
    assert env.server._try_reserve_run(session_id, "typed")
    try:
        with pytest.raises(RunSlotBusy):
            env.owner_turn.start_owner_turn(session_id, "main", "⏰ wake")
    finally:
        _clear(env.server, session_id)

    assert env.admitted == []
    assert env.started == []


def test_owner_turn_holds_the_slot_until_it_ends(owner_turn_env):
    env = owner_turn_env
    session_id = "owner-runs"
    _clear(env.server, session_id)
    try:
        execution_id = env.owner_turn.start_owner_turn(session_id, "main", "⏰ wake")

        assert execution_id == "e-owner"
        assert env.server._is_run_active(session_id)
        assert not env.server._try_reserve_run(session_id, "typed")
        with env.server._running_tasks_lock:
            assert env.server._running_tasks[session_id]["execution_id"] == "e-owner"

        # The turn ends (activation fails here): the slot is free again.
        env.started[0].target()
        assert env.failed == ["owner_turn_activation_failed"]
        assert not env.server._is_run_active(session_id)
    finally:
        _clear(env.server, session_id)


def test_an_owner_turn_stopped_before_it_starts_ends_without_a_result(owner_turn_env, monkeypatch):
    """Activation returning ``None`` (a stop won the race) frees the slot and fails nothing."""
    env = owner_turn_env
    session_id = "owner-stopped"
    done = []

    async def stopped(self, admission):
        return None

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter.activate",
        stopped, raising=False)
    _clear(env.server, session_id)
    try:
        env.owner_turn.start_owner_turn(session_id, "main", "⏰ wake", on_done=done.append)
        env.started[0].target()
        assert env.failed == []
        assert done == [None]
        assert not env.server._is_run_active(session_id)
    finally:
        _clear(env.server, session_id)


def test_an_admitted_turn_waits_for_the_slot_then_holds_it(owner_turn_env, monkeypatch):
    """A Goal's next turn is admitted while another turn holds the branch;
    it takes the slot once that turn gives it back, is announced as the
    conversation's running turn and frees the slot when it ends."""
    import threading
    from openprogram.runtime.conversation.run_control import run_slot

    env = owner_turn_env
    session_id = "goal-next"
    emitted: list[dict] = []
    monkeypatch.setattr(env.server, "_emit_running_task_event",
                        lambda _sid, **ids: emitted.append(ids))
    _clear(env.server, session_id)
    assert env.server._try_reserve_run(session_id, "notice")
    asked = []

    def still_queued():
        asked.append(1)
        if len(asked) == 3:
            env.server._release_run_reservation(session_id, "notice")
        return True

    try:
        assert run_slot.hold_admitted(
            session_id, "goal_user", execution_id="e-goal", status_version=2, branch_id=None,
            runner=threading.current_thread(), still_wanted=still_queued, poll_s=0)
        assert len(asked) >= 3
        with env.server._running_tasks_lock:
            task = dict(env.server._running_tasks[session_id])
        assert task["msg_id"] == "goal_user" and task["execution_id"] == "e-goal"
        assert not task.get("_reserved")
        assert env.server._is_run_active(session_id)
        assert not env.server._try_reserve_run(session_id, "typed", lock_wait=0)
        # Announced as the running turn: the frame names no cleared turn.
        assert emitted[-1] == {}

        run_slot.finish(session_id, "goal_user", execution_id="e-goal")
        assert emitted[-1] == {"cleared_msg_id": "goal_user", "cleared_execution_id": "e-goal"}
        assert not env.server._is_run_active(session_id)
    finally:
        _clear(env.server, session_id)


def test_an_admitted_turn_that_is_no_longer_queued_holds_nothing(owner_turn_env):
    import threading
    from openprogram.runtime.conversation.run_control import run_slot

    env = owner_turn_env
    session_id = "goal-stopped"
    _clear(env.server, session_id)
    assert not run_slot.hold_admitted(
        session_id, "goal_user", execution_id="e-goal", status_version=2, branch_id=None,
        runner=threading.current_thread(), still_wanted=lambda: False, poll_s=0)
    with env.server._running_tasks_lock:
        assert session_id not in env.server._running_tasks


def test_a_turn_on_an_unselected_branch_holds_that_branchs_slot(owner_turn_env, monkeypatch):
    """A Goal that follows a branch other than the selected one runs in
    that branch's slot and is announced as that branch's run, so the
    selected branch's composer stays free."""
    import threading
    from openprogram.interfaces.server.server_runtime import session_lifecycle
    from openprogram.runtime.conversation.run_control import run_slot

    env = owner_turn_env
    session_id = "goal-branch"
    branch_frames: list[tuple] = []
    monkeypatch.setattr(session_lifecycle, "_slot_of_branch", lambda _sid, branch: branch or "")
    monkeypatch.setattr(env.server, "_emit_branch_running_task_event",
                        lambda _sid, branch, **ids: branch_frames.append((branch, ids)))
    _clear(env.server, session_id)
    try:
        assert run_slot.hold_admitted(
            session_id, "goal_user", execution_id="e-goal", status_version=1, branch_id="branch_goal",
            runner=threading.current_thread(), still_wanted=lambda: True, poll_s=0)
        with env.server._running_tasks_lock:
            assert session_id not in env.server._running_tasks
            assert env.server._branch_running_tasks[(session_id, "branch_goal")]["execution_id"] == "e-goal"
        assert branch_frames == [("branch_goal", {})]

        run_slot.finish(session_id, "goal_user", execution_id="e-goal")
        assert branch_frames[-1] == ("branch_goal", {"cleared_msg_id": "goal_user",
                                                     "cleared_execution_id": "e-goal"})
        with env.server._running_tasks_lock:
            assert (session_id, "branch_goal") not in env.server._branch_running_tasks
    finally:
        with env.server._running_tasks_lock:
            env.server._branch_running_tasks.pop((session_id, "branch_goal"), None)
        _clear(env.server, session_id)


def test_an_admitted_turn_drops_a_slot_nothing_holds_any_more(owner_turn_env, monkeypatch):
    """A reservation left behind by a turn that never started keeps the
    slot only for its 300 seconds: a Goal's next turn, whose own queued
    record makes the run guard report the conversation as running, takes
    the slot instead of waiting for it forever."""
    import threading
    import time
    from openprogram.runtime.conversation.run_control import run_slot

    env = owner_turn_env
    session_id = "goal-zombie"
    emitted: list[dict] = []
    monkeypatch.setattr(env.server, "_emit_running_task_event",
                        lambda _sid, **ids: emitted.append(ids))
    _clear(env.server, session_id)
    with env.server._running_tasks_lock:
        env.server._running_tasks[session_id] = {
            "msg_id": "old", "_reserved": True, "started_at": time.time() - 400}
    asked: list[int] = []

    def wanted():
        asked.append(1)
        return len(asked) < 5

    try:
        assert run_slot.hold_admitted(
            session_id, "goal_user", execution_id="e-goal", status_version=2, branch_id=None,
            runner=threading.current_thread(), still_wanted=wanted, poll_s=0)
        with env.server._running_tasks_lock:
            assert env.server._running_tasks[session_id]["msg_id"] == "goal_user"
        assert {"cleared_msg_id": "old", "cleared_execution_id": None} in emitted
    finally:
        _clear(env.server, session_id)


def test_a_queued_turn_is_not_reported_while_another_turn_runs_in_the_slot(owner_turn_env, monkeypatch):
    """A background-command notice holds the slot and runs; a Goal's next
    turn waits behind it, queued with no attempt. The running turn the
    clients see, and their Stop names, is the notice's."""
    from openprogram.runtime import execution as execution_package

    env = owner_turn_env
    session_id = "notice-first"
    _clear(env.server, session_id)
    monkeypatch.setattr(env.server, "_canonical_foreground_task", lambda _sid: {
        "msg_id": "goal_user", "execution_id": "e-goal", "status": "queued", "status_version": 1})
    attempt = {"id": None}
    monkeypatch.setattr(execution_package, "default_store", lambda: SimpleNamespace(
        get_execution=lambda _id: SimpleNamespace(current_attempt_id=attempt["id"])))
    with env.server._running_tasks_lock:
        env.server._running_tasks[session_id] = {
            "msg_id": "notice_user", "execution_id": "e-notice", "status_version": 3, "op_kind": "reply"}
    try:
        identity = env.server._run_identity(session_id)
        assert identity["msg_id"] == "notice_user"
        assert identity["execution_id"] == "e-notice"
        assert identity["status_version"] == 3

        # Once the Goal's turn has started, it is the one reported.
        attempt["id"] = "a1"
        assert env.server._run_identity(session_id)["msg_id"] == "goal_user"
    finally:
        _clear(env.server, session_id)


def test_a_notice_on_an_unselected_branch_does_not_wait_for_a_held_lock(owner_turn_env, monkeypatch, tmp_path):
    """``lock_wait=0`` waits for nothing on another branch either: a
    conversation whose lock is held gets the notice at the next sweep."""
    import threading
    import time
    from openprogram.interfaces.server.server_runtime import session_lifecycle
    from openprogram.state.store.session.files.session_lock import SessionLockTimeout, session_interprocess_lock

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: str(tmp_path))
    monkeypatch.setattr(session_lifecycle, "_slot_of_branch", lambda _sid, branch: branch or "")
    held, done = threading.Event(), threading.Event()

    def hold():
        with session_interprocess_lock("locked-notice"):
            held.set()
            done.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        try:
            reserved = session_lifecycle.ServerRunSlot().reserve(
                "locked-notice", "m", lock_wait=0, branch_id="X")
        except SessionLockTimeout:
            reserved = False
        assert reserved is False
        assert time.monotonic() - started < 1
    finally:
        done.set()
        holder.join()
