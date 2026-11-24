"""Loops that wait on something busy wake on the event that ends the wait,
or at its deadline, instead of looking on a short timer."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from openprogram.host._platform import changes


# -- a conversation going quiet ---------------------------------------------

def test_a_retired_turn_token_announces_its_session_once():
    from openprogram.runtime.execution.run_control.tokens import TURN_ENDED_TOPIC, CancellationToken

    token = CancellationToken("s-quiet")
    with changes.listen(TURN_ENDED_TOPIC) as listener:
        token.retire()
        assert listener.wait(0) == {f"{TURN_ENDED_TOPIC}:s-quiet"}
        token.retire()
        assert listener.wait(0) == set()


def test_idle_sources_call_back_and_other_commits_do_not():
    from openprogram.runtime.conversation.run_control.idle import subscribe_idle
    from openprogram.runtime.execution.run_control.tokens import turn_ended
    from openprogram.runtime.jobs.store import RUNS_TOPIC

    calls: list[int] = []
    unsubscribe = subscribe_idle(lambda: calls.append(1))
    try:
        changes.signal("run_tables", "commands", "execution_events")
        assert calls == []
        changes.signal("run_tables", "executions")
        changes.signal(RUNS_TOPIC, "s1")
        turn_ended("s1")
        assert len(calls) == 3
    finally:
        unsubscribe()
    turn_ended("s1")
    assert len(calls) == 3


# -- job budgets --------------------------------------------------------------

class _Budgets:
    """The budget loop's arithmetic, without a runner or its threads."""

    def __init__(self, now: float, jobs: dict, *, real_clock: bool = True, poll=None):
        from openprogram.runtime.jobs.runner.progress import ProgressOperations

        class Runner(ProgressOperations):
            pass

        self.runner = runner = Runner()
        runner._lock = threading.Lock()
        runner._jobs = jobs
        runner._monotonic = lambda: now
        runner._budget_real_clock = real_clock
        runner._budget_poll_seconds = poll


def _entry(started: float, *, runtime=None, idle=None, last=None) -> dict:
    return {"session_id": "s", "started_monotonic": started,
            "last_activity_monotonic": started if last is None else last,
            "time_limits": (runtime, idle)}


def test_the_budget_loop_sleeps_until_the_earliest_deadline(monkeypatch):
    from openprogram.runtime.jobs import delegation_watch as watch
    monkeypatch.setattr(watch, "max_run_seconds", lambda: 0.0)
    monkeypatch.setattr(watch, "stall_seconds", lambda: 0.0)
    jobs = {"a": _entry(100.0, runtime=50.0), "b": _entry(100.0, idle=8.0, last=105.0)}
    budgets = _Budgets(110.0, jobs).runner
    budgets._delegation_checked_at = 110.0
    # b goes idle at 113: three seconds (and a hair) from now.
    assert 3.0 <= budgets._budget_timeout() < 3.1
    jobs["b"]["budget_cancelled"] = True
    assert 40.0 <= budgets._budget_timeout() < 40.1


def test_with_no_deadline_the_budget_loop_waits_for_the_resync(monkeypatch):
    from openprogram.runtime.jobs import delegation_watch as watch
    from openprogram.runtime.jobs.runner import shared
    monkeypatch.setattr(watch, "max_run_seconds", lambda: 0.0)
    monkeypatch.setattr(watch, "stall_seconds", lambda: 0.0)
    budgets = _Budgets(10.0, {"a": _entry(0.0)}).runner
    budgets._delegation_checked_at = 10.0
    assert budgets._budget_timeout() == shared._DISPATCH_RESYNC_SECS


def test_an_injected_clock_is_read_on_its_poll(monkeypatch):
    budgets = _Budgets(0.0, {"a": _entry(0.0, runtime=1.0)}, real_clock=False, poll=0.01).runner
    assert budgets._budget_timeout() == 0.01


def test_stall_and_hard_stop_are_deadlines_and_usage_asks_for_a_spend_check(monkeypatch):
    from openprogram.runtime.jobs import delegation_watch as watch
    monkeypatch.setattr(watch, "max_run_seconds", lambda: 7200.0)
    monkeypatch.setattr(watch, "stall_seconds", lambda: 600.0)
    budgets = _Budgets(1000.0, {"a": _entry(900.0, last=950.0)}).runner
    # Never checked: look now.
    assert budgets._delegation_due() == 1000.0
    budgets._delegation_checked_at = 1000.0
    assert budgets._delegation_due() == 950.0 + 600.0
    budgets._delegation_memory = {"stalled": {"a"}, "stopped": set()}
    assert budgets._delegation_due() == 900.0 + 7200.0
    budgets._delegation_spend_changed = True
    assert budgets._delegation_due() == 1000.0 + watch.CHECK_EVERY_S


# -- self-update watchdog -----------------------------------------------------

def test_the_watchdog_follows_the_controller_and_relaunches_one_that_ended(tmp_path, monkeypatch):
    from openprogram.host._platform import process_exits
    from openprogram.operations.self_update.control import switch, watchdog

    record = SimpleNamespace(request=SimpleNamespace(update_id="u1"),
                             state=SimpleNamespace(phase="building"))
    store = SimpleNamespace(root=tmp_path / "self-updates", load_active=lambda: record)
    store.root.mkdir()
    launched: list[str] = []
    followed: list[tuple] = []
    now = [1000.0]
    monkeypatch.setattr(switch, "clear_finished_admission", lambda *_a: None)
    monkeypatch.setattr(switch, "owns", lambda _record: True)
    monkeypatch.setattr(watchdog, "is_terminal", lambda _phase: False)
    monkeypatch.setattr(watchdog, "drain", lambda store: None)
    monkeypatch.setattr(switch, "launch", lambda update_id, store: launched.append(update_id))
    monkeypatch.setattr(process_exits, "watch_process", lambda *args, **_kw: followed.append(args))
    monkeypatch.setattr(watchdog, "_launched", {})
    import openprogram.operations.self_update.repair.diagnosis as diagnosis
    monkeypatch.setattr(diagnosis, "dispatch_pending", lambda: None)
    monkeypatch.setattr(watchdog.time, "time", lambda: now[0])

    # No controller: one is launched and given a while to say who it is.
    monkeypatch.setattr(switch, "controller_process", lambda _s, _r: None)
    assert watchdog.tick(store) == 1000.0 + switch.CONTROLLER_START_SECONDS
    assert launched == ["u1"]
    # A file change meanwhile launches no second one.
    now[0] = 1005.0
    assert watchdog.tick(store) == 1000.0 + switch.CONTROLLER_START_SECONDS
    assert launched == ["u1"]
    # It runs: its exit is followed, with no deadline.
    monkeypatch.setattr(switch, "controller_process", lambda _s, _r: (4321, "token"))
    assert watchdog.tick(store) is None
    assert followed == [(4321, "token", watchdog.SELF_UPDATE_TOPIC, "u1")]
    # It ended, after its start window: launched again at once.
    now[0] = 1100.0
    monkeypatch.setattr(switch, "controller_process", lambda _s, _r: None)
    assert watchdog.tick(store) == 1100.0 + switch.CONTROLLER_START_SECONDS
    assert launched == ["u1", "u1"]
    # Nothing running: only a file change, an exit or the resync.
    monkeypatch.setattr(switch, "owns", lambda _record: False)
    assert watchdog.tick(store) is None


def test_the_controller_claim_names_a_running_process_only(tmp_path, monkeypatch):
    from openprogram.operations.self_update.control import switch

    record = SimpleNamespace(request=SimpleNamespace(update_id="u1"))
    store = SimpleNamespace(root=tmp_path)
    (tmp_path / "u1").mkdir()
    assert switch.controller_process(store, record) is None
    (tmp_path / "u1" / switch.CONTROLLER).write_text('{"pid": 77, "started": "birth"}', encoding="utf-8")
    monkeypatch.setattr("openprogram.host._platform.processes.process_start_token",
                        lambda pid: "birth" if pid == 77 else None)
    assert switch.controller_process(store, record) == (77, "birth")
    monkeypatch.setattr("openprogram.host._platform.processes.process_start_token", lambda pid: "someone else")
    assert switch.controller_process(store, record) is None, "a reused pid is not the controller"


def test_the_self_update_store_is_watched_once(tmp_path):
    from openprogram.operations.self_update.control import watchdog

    watchdog.watch_store(SimpleNamespace(root=tmp_path))
    watchdog.watch_store(SimpleNamespace(root=tmp_path))
    try:
        entries = [w for w in changes._watched if w.path == changes._real(Path(tmp_path))]
        assert [(w.topic, w.recursive) for w in entries] == [(watchdog.SELF_UPDATE_TOPIC, True)]
    finally:
        changes.unwatch(tmp_path, watchdog.SELF_UPDATE_TOPIC)


# -- memory -------------------------------------------------------------------

def test_the_idle_session_watcher_sleeps_until_its_next_deadline(monkeypatch):
    from openprogram.state.memory import session_watcher

    monkeypatch.setattr(session_watcher.time, "time", lambda: 1000.0)
    monkeypatch.setattr(session_watcher, "_next_due", None)
    assert session_watcher._wait_after_scan(300) == 300
    monkeypatch.setattr(session_watcher, "_next_due", 1040.0)
    assert session_watcher._wait_after_scan(300) == 41.0
    monkeypatch.setattr(session_watcher, "_next_due", 5000.0)
    assert session_watcher._wait_after_scan(300) == 300
