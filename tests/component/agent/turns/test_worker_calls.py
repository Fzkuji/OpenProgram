"""A function subprocess hands run-table work to its parent."""
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from openprogram.runtime.execution import worker_calls as module
from openprogram.runtime.execution.worker_calls import SubprocessWorkerCalls, WorkerCallServer

_ADD = f"{__name__}:add"
_FAIL = f"{__name__}:fail"
_CALLS_BACK = f"{__name__}:calls_back"
_UNPICKLABLE = f"{__name__}:unpicklable"
_observed = []


def add(left, right=0):
    return left + right


def fail():
    raise ValueError("refused")


def calls_back(callback=None):
    callback("job")
    _observed.append("after callback")
    return "done"


def unpicklable():
    return lambda: None


@pytest.fixture
def channel(monkeypatch):
    monkeypatch.setattr(module, "_WORKER_FUNCTIONS", frozenset({_ADD, _FAIL, _CALLS_BACK, _UNPICKLABLE}))
    _observed.clear()
    up, down = queue.Queue(), queue.Queue()
    calls = SubprocessWorkerCalls(up.put)
    server = WorkerCallServer(down, lambda: True)

    def pump(source, handler):
        while (message := source.get()) is not None:
            assert handler(message)

    threads = [threading.Thread(target=pump, args=(up, server.handle_message), daemon=True),
               threading.Thread(target=pump, args=(down, calls.handle_message), daemon=True)]
    for thread in threads:
        thread.start()
    yield calls
    up.put(None)
    down.put(None)


def test_a_call_returns_the_parents_value(channel):
    assert channel.call(_ADD, 2, right=3) == 5


def test_a_call_raises_the_parents_exception(channel):
    with pytest.raises(ValueError, match="refused"):
        channel.call(_FAIL)


def test_a_value_that_cannot_cross_is_an_error_not_a_hang(channel):
    with pytest.raises(RuntimeError, match="function"):
        channel.call(_UNPICKLABLE)


def test_only_listed_functions_can_be_called(channel):
    with pytest.raises(PermissionError):
        channel.call("os:getcwd")


def test_the_parent_waits_for_the_callback(channel):
    def callback(value):
        _observed.append(f"callback {value}")

    assert channel.call(_CALLS_BACK, callback=callback) == "done"
    assert _observed == ["callback job", "after callback"]


def test_a_failed_callback_fails_the_parents_call(channel):
    def callback(_value):
        raise OSError("placeholder not written")

    with pytest.raises(RuntimeError, match="placeholder not written"):
        channel.call(_CALLS_BACK, callback=callback)
    assert _observed == []


def test_get_runner_in_a_subprocess_is_the_stand_in(monkeypatch):
    from openprogram.runtime.jobs.runner import shared
    from openprogram.runtime.jobs.runner.worker_runner import WorkerRunner

    calls = SimpleNamespace(call=lambda function, *args, callback=None, **kwargs: (args, callback, kwargs))
    monkeypatch.setattr(module, "_calls", calls)
    runner = shared.get_runner()
    assert isinstance(runner, WorkerRunner)
    assert runner.can_borrow_current_claim("session")
    with runner.claim_only("job"):
        pass
    accepted = lambda job: None
    args, callback, kwargs = runner.spawn_job("session", on_accepted=accepted, borrow_current_claim=True, wait=True)
    assert args == ("spawn_job", "session") and callback is accepted and kwargs == {"wait": True}
    assert runner.await_job("job", timeout=3)[0] == ("await_job", "job")
    with pytest.raises(AttributeError):
        runner.record_job_activity


def test_a_gone_parent_ends_waiting_and_later_calls():
    calls = SubprocessWorkerCalls(lambda message: None)
    finished = []

    def wait():
        with pytest.raises(module.WorkerUnavailable, match="stopped"):
            calls.call(_ADD, 1)
        finished.append(True)

    waiting = threading.Thread(target=wait)
    waiting.start()
    while not calls._pending:
        time.sleep(0.01)
    calls.parent_gone("the Worker stopped")
    waiting.join(5)
    assert finished == [True]
    with pytest.raises(module.WorkerUnavailable, match="stopped"):
        calls.call(_ADD, 1)


def test_a_gone_parent_still_ends_the_calls_after_one_already_answered():
    from concurrent.futures import Future

    calls = SubprocessWorkerCalls(lambda message: None)
    answered, waiting = Future(), Future()
    answered.set_result("done")
    calls._pending.update(answered=answered, waiting=waiting)
    calls.parent_gone("the Worker stopped")
    assert answered.result() == "done"
    with pytest.raises(module.WorkerUnavailable, match="stopped"):
        waiting.result(0)


def test_only_a_read_only_process_hands_over_its_own_rows(monkeypatch):
    from openprogram.runtime.execution.store import run_tables

    calls = SubprocessWorkerCalls(lambda message: None)
    monkeypatch.setattr(module, "_calls", calls)
    assert module.worker_calls() is calls and module.run_table_calls() is None
    run_tables.this_process_only_reads_run_tables()
    assert module.run_table_calls() is calls


@pytest.mark.parametrize("change, inherits", [
    ({}, True),
    ({"source": "self_update_verify"}, False),
    ({"caller_session_id": "another-conversation"}, False),
    ({"authority_tier": "guest"}, False),
    ({"principal_id": "someone-else"}, False),
])
def test_a_scheduled_runs_sub_agent_takes_only_its_owners_policy(monkeypatch, change, inherits):
    from openprogram.runtime.conversation import session_config
    from openprogram.runtime.jobs.runner import admission, shared
    from openprogram.runtime.permissions.authority import local_owner_authority

    owner = local_owner_authority()
    monkeypatch.setattr(session_config, "conversation_permission",
                        lambda session_id: {"permission_mode": "bypass", "permission_rules": None})
    job = SimpleNamespace(**{"source": "agent_spawn", "caller_session_id": None, "parent_session_id": "task",
                             **owner, **change})
    token = shared.scheduled_caller.set({"session_id": "task", "principal_id": owner["principal_id"],
                                         "authority_tier": "owner"})
    try:
        snapshot = admission._scheduled_permission_snapshot(job)
    finally:
        shared.scheduled_caller.reset(token)
    assert snapshot == ({"mode": "bypass", "rules": None} if inherits else None)


def test_a_sub_agent_a_scheduled_run_starts_has_no_window_either():
    from openprogram.runtime.jobs.runner import admission, shared

    assert admission._started_by_detached_turn() is False
    token = shared.scheduled_caller.set({"session_id": "task", "principal_id": "p", "authority_tier": "owner"})
    try:
        assert admission._started_by_detached_turn() is True
    finally:
        shared.scheduled_caller.reset(token)
