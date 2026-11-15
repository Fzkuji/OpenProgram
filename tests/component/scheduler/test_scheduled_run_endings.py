"""A scheduled run ends once, wherever it ends: a pause is not an ending."""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import subprocess
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

from openprogram.operations.self_update.control import held_turns, maintenance
from openprogram.programs.tools.jobs.cron import cron, worker
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation import turn_runtime
from openprogram.runtime.conversation.production_driver import AgentProductionDriver
from openprogram.runtime.conversation.turn_runtime.types import TurnResult
from openprogram.runtime import execution as execution_package
from openprogram.host._platform.processes import process_start_token
from openprogram.runtime.execution import hooks, process_owner, restart
from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.checkpoints import CheckpointFragment
from openprogram.runtime.execution.control import RuntimeControlService
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.effects import EffectClassification, EffectStatus, EffectStore
from openprogram.runtime.execution.event_effects import ExecutionEventEffects
from openprogram.runtime.execution.model import CapabilitySet, ExecutionStatus
from openprogram.runtime.execution.runtime_contract import RuntimeContractError
from openprogram.runtime.execution.store import ExecutionStore
from openprogram.runtime.execution.store import run_tables
from openprogram.runtime.scheduler import run_finish, service as scheduler_service, task_branch, task_runs, time_limits

TASK = {"id": "t1", "title": "VPS stock"}
CHECK_IN = "runtime/always_on"
SCHEDULER = "runtime/scheduler"
SESSION = "local_s1"
PAUSED = SimpleNamespace(status=ExecutionStatus.PAUSED)
COMPLETED = SimpleNamespace(status=ExecutionStatus.COMPLETED)
FAILURE = SimpleNamespace(failed=True, error="the page would not open")


@pytest.fixture
def world(tmp_path, monkeypatch):
    from openprogram.runtime import interface_link
    from openprogram.runtime.conversation.always_on import runtime as always_on

    monkeypatch.setattr(cron, "_resolve_path", lambda: str(tmp_path / "tasks.json"))
    monkeypatch.setattr(worker, "_resolve_path", lambda: str(tmp_path / "tasks.json"))
    cron._save(str(tmp_path / "tasks.json"), [dict(TASK)])
    db = SessionDB(tmp_path / "sessions")
    db.create_session(SESSION, "main", source="web")
    db.append_message(SESSION, {"id": "u0", "role": "user", "content": "hi", "predecessor": None, "timestamp": 0.1})
    db.append_message(SESSION, {"id": "a0", "role": "assistant", "content": "hello", "predecessor": "u0",
                                "timestamp": 0.2})
    db.set_head(SESSION, "a0")
    monkeypatch.setattr(task_branch, "_db", lambda: db)
    monkeypatch.setattr(interface_link, "branches_changed", lambda session_id: None)
    reported: list[tuple] = []
    monkeypatch.setattr(always_on, "report_scheduled", lambda *arguments, **_files: reported.append(arguments))
    return SimpleNamespace(db=db, reported=reported, root=tmp_path)


def _reply(text, *, tip="reply"):
    return SimpleNamespace(final_text=text, failed=False, error=None, assistant_msg_id=tip)


def _handoff():
    """What a turn returns when it stopped at a safe point."""
    result = _reply("", tip="half")
    result._execution_safe_point_handoff = True
    return result


def _kept(world):
    directory = world.root / "run-endings"
    return sorted(path.name for path in directory.iterdir()) if directory.exists() else []


def _admit(store, *, text="check", scheduled_task=TASK, message="user", speaker=None, parent=None, source=None):
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    # A check-in is admitted as a web turn.
    request = {"user_text": text, "agent_id": "main", "source": source or ("web" if speaker == CHECK_IN else "scheduler")}
    if scheduled_task:
        request["scheduled_task"] = scheduled_task
    if speaker:
        request["speaker_id"] = speaker
    payload = {"version": 1, "kind": "chat", "request": request}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return store.admit_execution(
        session_id=SESSION, revision_id=revision.revision_id, input_ref=f"agent-turn:{digest}", input_hash=digest,
        entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id=message, assistant_message_id=f"{message}_reply", parent_execution_id=parent,
        capabilities=CapabilitySet(pause=True, safe_point_kinds=("action.after",), state_schema_version=1),
        agent_turn_payload=payload,
    )


def _ran_turn(tmp_path, *, scheduled_task=TASK, speaker=None, source=None):
    """A real turn, activated, whose admitted input names its task."""
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    execution = _admit(store, scheduled_task=scheduled_task, speaker=speaker, source=source)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(execution.execution_id, expected_version=execution.status_version,
                                      owner_id="worker", attempt_id="attempt")
    active, _running = attempts.activate(leased.attempt_id, generation=leased.generation,
                                         expected_execution_version=reserved.status_version)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    driver = AgentProductionDriver(store, control_service=service, run_ended=run_finish.finish_resumed)
    return driver, active, store


def _lose_owner(driver, active, store):
    """The Worker died: the turn is left paused for the next one."""
    driver.control_service.recover_owner_loss(active.execution_id, attempt_id=active.attempt_id,
                                              generation=active.generation)
    paused = store.get_execution(active.execution_id)
    assert paused.status is ExecutionStatus.PAUSED
    return paused


def _next_attempt(store, execution):
    """The turn goes on under a newer owner."""
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(execution.execution_id, expected_version=execution.status_version,
                                      owner_id="worker2", attempt_id="attempt2")
    return attempts.activate(leased.attempt_id, generation=leased.generation,
                             expected_execution_version=reserved.status_version)[0]


def _cancel(driver, active, store):
    execution = store.get_execution(active.execution_id)
    asyncio.run(driver.control_service.request_cancel(
        command_id="stop", execution_id=execution.execution_id, expected_version=execution.status_version,
        actor={"surface": "test"}, reason_code="user_cancelled"))
    event = threading.Event()
    event.set()
    return event


def _restart_runner(driver, store, monkeypatch, *, window=-1):
    monkeypatch.setattr(restart, "window_seconds", lambda: window)
    monkeypatch.setattr(maintenance, "maintenance_blocks", lambda *_: False)
    monkeypatch.setattr(execution_package, "default_control_service", lambda: driver.control_service)
    return SimpleNamespace(_execution_store=store, _restart_shutdown=False, _execution_control=driver.control_service)


def _runs(status_only=False):
    return [run["status"] if status_only else (run["status"], run["text"]) for run in task_runs.recent("t1")]


def _child_run(monkeypatch, world, result, execution):
    """One scheduled job's child, up to its adapter; returns what it told the Worker."""
    from openprogram.runtime.conversation import production_driver
    from openprogram.runtime.permissions import rule

    admitted: list = []

    class Adapter:
        store = SimpleNamespace(get_execution=lambda _execution_id: execution)

        def admit(self, request, **_arguments):
            admitted.append(request)
            return SimpleNamespace(execution_id="run-1")

        async def activate(self, _admission):
            return None, result

    spec = {"session_id": SESSION, "agent_id": "main", "prompt": "check",
            "job_authority": {"principal_id": "owner", "authority_tier": "owner"}}
    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", Adapter)
    monkeypatch.setattr(worker, "_verify_execution_spec", lambda value: (value, None))
    monkeypatch.setattr(worker, "_conversation_settings", lambda _session_id, _branch_id=None: ("ask", None))
    monkeypatch.setattr(worker._sandbox, "resolve_policy", lambda **_arguments: None)
    monkeypatch.setattr(worker._sandbox, "install_policy_snapshot", lambda _snapshot: None)
    monkeypatch.setattr(rule, "load_merged_rules", lambda _session_id: None)
    frames: list = []
    kept_when_told: list = []

    def tell_worker(frame):
        kept_when_told.append(_kept(world))
        frames.append(frame)

    log_path = world.root / "run.log"
    worker._run_prompt_job(spec, str(log_path), task=TASK, notices=SimpleNamespace(put=tell_worker))
    return SimpleNamespace(frames=frames, kept_when_told=kept_when_told, request=admitted[0], log=log_path.read_text())


def test_a_paused_child_records_nothing_and_says_nothing(world, monkeypatch):
    run = _child_run(monkeypatch, world, _handoff(), PAUSED)
    assert run.frames == [] and _kept(world) == []
    assert task_runs.recent("t1") == [] and world.reported == []
    assert "# not recorded" in run.log
    # The admitted input carries the task, for whoever continues the turn.
    assert run.request.scheduled_task == TASK
    # The run is still the branch's newest node, ready to be continued.
    assert world.db.get_branch_meta(SESSION, run.request.user_msg_id)[task_branch.TASK_FIELD] == "t1"


def test_a_child_that_lost_its_owner_returns_nothing_and_records_nothing(world, monkeypatch):
    run = _child_run(monkeypatch, world, None, PAUSED)
    assert run.frames == [] and _kept(world) == []
    assert task_runs.recent("t1") == [] and world.reported == []


@pytest.mark.parametrize("result,status", [
    (_handoff(), COMPLETED),
    (_reply("late"), PAUSED),
    (None, PAUSED),
    (None, SimpleNamespace(status=ExecutionStatus.PAUSING)),
    (_reply("late"), SimpleNamespace(status=ExecutionStatus.RUNNING)),
    (_reply(""), SimpleNamespace(status=ExecutionStatus.QUEUED)),
    (_reply("text so far"), SimpleNamespace(status=ExecutionStatus.RECONCILIATION_REQUIRED)),
    (_reply("late"), SimpleNamespace(status=ExecutionStatus.CANCELLING)),
])
def test_a_stop_it_resumes_from_is_not_an_ending(world, result, status):
    assert run_finish.conclude(SESSION, TASK, "run-1", result, status) is None
    assert task_runs.recent("t1") == [] and world.reported == []


@pytest.mark.parametrize("reply", ["", "   \n\t ", "\u200b", "\u200b\u200d\ufeff"])
def test_an_empty_reply_counts_as_nothing_new(world, reply):
    outcome = run_finish.conclude(SESSION, TASK, "run-1", _reply(reply), COMPLETED)
    assert outcome["status"] == task_runs.QUIET and outcome["text"] == ""
    run_finish.finish(outcome)
    assert [run["status"] for run in task_runs.recent("t1")] == [task_runs.QUIET]
    assert world.reported == []


def test_a_child_that_ended_keeps_its_ending_then_tells_the_worker(world, monkeypatch):
    run = _child_run(monkeypatch, world, _reply("In stock"), COMPLETED)
    assert run.frames[0]["__op_scheduled_run__"] is True and run.frames[0]["status"] == task_runs.RESULT
    # On disk before the Worker hears, so a Worker that has gone loses nothing.
    assert run.kept_when_told == [["run-1.json"]]
    run_finish.finish(run.frames[0])
    assert _kept(world) == [] and len(world.reported) == 1


def test_a_resumed_run_with_news_is_recorded_and_tells_the_user_once(world, tmp_path):
    driver, active, store = _ran_turn(tmp_path)
    driver._finish_attempt(active, _reply("In stock at $5"), threading.Event())
    assert store.get_execution(active.execution_id).status is ExecutionStatus.COMPLETED
    runs = task_runs.recent("t1")
    assert [(run["status"], run["text"]) for run in runs] == [(task_runs.RESULT, "In stock at $5")]
    assert world.reported == [(SESSION, "t1", "VPS stock", "In stock at $5")]


def test_a_resumed_run_that_found_nothing_new_posts_nothing(world, tmp_path):
    driver, active, _store = _ran_turn(tmp_path)
    driver._finish_attempt(active, _reply(task_branch.QUIET_MARK), threading.Event())
    assert [run["status"] for run in task_runs.recent("t1")] == [task_runs.QUIET]
    assert world.reported == []


def test_a_resumed_run_that_failed_counts_like_any_failed_run(world, tmp_path):
    driver, active, _store = _ran_turn(tmp_path)
    failure = SimpleNamespace(failed=True, error="the page would not open")
    driver._finish_attempt(active, failure, threading.Event())
    assert [(run["status"], run["text"]) for run in task_runs.recent("t1")] == [
        (task_runs.FAILED, "the page would not open")]
    assert world.reported == []


def test_a_resumed_run_that_pauses_again_is_still_not_an_ending(world, tmp_path):
    driver, active, store = _ran_turn(tmp_path)
    driver._finish_attempt(active, _handoff(), threading.Event())
    assert store.get_execution(active.execution_id).status is ExecutionStatus.RUNNING
    assert task_runs.recent("t1") == [] and world.reported == []


@pytest.mark.parametrize("result", [_reply("late"), _handoff(), None])
def test_the_resumed_finish_records_nothing_for_a_paused_turn(world, tmp_path, result):
    driver, active, store = _ran_turn(tmp_path)
    paused = _lose_owner(driver, active, store)
    run_finish.finish_resumed(store, paused, result)
    assert task_runs.recent("t1") == [] and world.reported == []


def test_a_stop_waiting_on_an_external_effect_is_not_the_ending(world, tmp_path):
    driver, active, store = _ran_turn(tmp_path)
    effects = EffectStore(store)
    planned = effects.register(effect_id="send", execution_id=active.execution_id, attempt_id=active.attempt_id,
                               action_id="send", classification=EffectClassification.NONREPEATABLE,
                               idempotency_key=None, metadata={})
    dispatched = effects.mark_dispatched(planned.effect_id, expected_status=planned.status)
    driver._finish_attempt(active, _reply("text at first stop"), threading.Event())
    assert store.get_execution(active.execution_id).status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert task_runs.recent("t1") == [] and world.reported == []
    resolved = driver.control_service.resolve_effect(effect_id="send", expected_status=dispatched.status,
                                                     outcome=EffectStatus.COMMITTED, receipt={"ok": True})
    assert resolved.execution.status is ExecutionStatus.PAUSED
    second = _next_attempt(store, resolved.execution)
    driver._finish_attempt(second, _reply("the real ending"), threading.Event())
    assert _runs() == [(task_runs.RESULT, "the real ending")]
    assert world.reported == [(SESSION, "t1", "VPS stock", "the real ending")]


def test_a_late_return_from_a_replaced_owner_leaves_the_real_ending_to_report(world, tmp_path):
    driver, active, store = _ran_turn(tmp_path)
    second = _next_attempt(store, _lose_owner(driver, active, store))
    driver._finish_attempt(active, _reply(""), threading.Event())
    running = store.get_execution(active.execution_id)
    assert running.status is ExecutionStatus.RUNNING
    assert run_finish.conclude(SESSION, TASK, active.execution_id, _reply(""), running) is None
    driver._finish_attempt(second, _reply("In stock at $5"), threading.Event())
    assert _runs() == [(task_runs.RESULT, "In stock at $5")]
    assert len(world.reported) == 1


def test_a_turn_already_ended_still_reports_what_it_returned(world, tmp_path):
    driver, active, store = _ran_turn(tmp_path)
    running = store.get_execution(active.execution_id)
    driver.control_service.finish_attempt(attempt_id=active.attempt_id, generation=active.generation,
                                          expected_execution_version=running.status_version,
                                          target=ExecutionStatus.COMPLETED, outcome="completed")
    driver._finish_attempt(active, _reply("In stock"), threading.Event())
    assert _runs() == [(task_runs.RESULT, "In stock")]
    assert len(world.reported) == 1


@pytest.mark.parametrize("result", [_reply("Half a sent"), None, _reply("")])
def test_a_run_the_user_stopped_is_no_news_and_no_failure(world, tmp_path, result):
    task_runs.record("t1", task_runs.FAILED, "broke", execution_id="earlier-1")
    task_runs.record("t1", task_runs.FAILED, "broke", execution_id="earlier-2")
    driver, active, store = _ran_turn(tmp_path)
    driver._finish_attempt(active, result, _cancel(driver, active, store))
    assert store.get_execution(active.execution_id).status is ExecutionStatus.CANCELLED
    assert _runs(status_only=True) == [task_runs.FAILED, task_runs.FAILED] and world.reported == []
    # The two failures before it still count toward the notice.
    assert task_runs.record("t1", task_runs.FAILED, "broke") == task_runs.FAILURE_NOTICE


def test_a_run_that_ended_closes_the_pages_it_opened(world, monkeypatch):
    from openprogram.programs._host import services
    closed = []
    browser = SimpleNamespace(tabs=SimpleNamespace(close_run_tabs_soon=closed.append))
    monkeypatch.setattr(services, "lookup", lambda name: browser if name == "browser" else None)
    run_finish.finish(run_finish.conclude(SESSION, TASK, "run-1", _reply(""), COMPLETED))
    assert closed == ["run-1"]
    # An ending kept on disk for a Worker that had gone closes them too.
    closed.clear()
    run_finish._finish(dict(run_finish.conclude(SESSION, TASK, "run-2", _reply(""), COMPLETED)))
    assert closed == ["run-2"]


def test_any_execution_that_ends_has_its_run_pages_closed(monkeypatch):
    """Stopped by the user, failed or cut off: the terminal settlement closes what the run opened."""
    from openprogram.programs._host import services
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.conversation import execution_hooks
    closed = []
    browser = SimpleNamespace(tabs=SimpleNamespace(close_run_tabs_soon=closed.append))
    monkeypatch.setattr(services, "lookup", lambda name: browser if name == "browser" else None)
    monkeypatch.setattr(chat, "after_terminal", lambda executions, execution: None)
    execution_hooks._settle_terminal(None, SimpleNamespace(execution_id="exec_9", status=ExecutionStatus.CANCELLED))
    assert closed == ["exec_9"]


@pytest.mark.parametrize("status", [ExecutionStatus.FAILED, ExecutionStatus.INTERRUPTED])
def test_a_run_that_ended_broken_is_a_failure_even_with_an_empty_reply(world, status):
    outcome = run_finish.conclude(SESSION, TASK, "run-1", _reply(""), SimpleNamespace(status=status))
    assert outcome["status"] == task_runs.FAILED


def test_the_third_failure_in_a_row_tells_the_user_once(world):
    for index in range(4):
        run_finish.finish(run_finish.conclude(
            SESSION, TASK, f"run-{index}", _reply(f"{task_branch.UNABLE_MARK} the page would not open"), COMPLETED))
    assert _runs(status_only=True) == [task_runs.FAILED] * 4
    assert [row[3] for row in world.reported] == [
        '"VPS stock" did not finish 3 times in a row: the page would not open']


def test_a_resumed_failure_counts_toward_the_same_notice(world, tmp_path):
    task_runs.record("t1", task_runs.FAILED, "broke", execution_id="earlier-1")
    task_runs.record("t1", task_runs.FAILED, "broke", execution_id="earlier-2")
    driver, active, _store = _ran_turn(tmp_path)
    driver._finish_attempt(active, FAILURE, threading.Event())
    assert len(world.reported) == 1 and "3 times" in world.reported[0][3]


def test_a_task_deleted_meanwhile_gets_no_record_and_no_message(world, tmp_path):
    assert scheduler_service.delete_task("t1")
    outcome = run_finish.conclude(SESSION, TASK, "kept-run", _reply("In stock"), COMPLETED)
    run_finish.keep(outcome)
    driver, active, _store = _ran_turn(tmp_path)
    driver._finish_attempt(active, _reply("In stock"), threading.Event())
    run_finish.finish_kept()
    assert task_runs.recent("t1") == [] and world.reported == [] and _kept(world) == []


def test_a_turn_held_for_a_version_switch_ends_only_when_its_replay_does(world, monkeypatch):
    held = TurnResult(final_text=held_turns.HELD_MESSAGE, user_msg_id="u1", assistant_msg_id="u1_reply",
                      error_reason=held_turns.HELD_REASON)
    run = _child_run(monkeypatch, world, held, COMPLETED)
    assert run.frames == [] and _kept(world) == [] and task_runs.recent("t1") == []
    monkeypatch.setattr(turn_runtime, "process_user_turn", lambda *_a, **_k: _reply("Real news: stock is back"))
    saved = held_turns.encode(TurnRequest(session_id=SESSION, user_text="check", agent_id="main", source="scheduler",
                                          user_msg_id="u1", scheduled_task=TASK))
    held_turns._run_one(saved)
    held_turns._run_one(saved)
    assert _runs() == [(task_runs.RESULT, "Real news: stock is back")]
    assert world.reported == [(SESSION, "t1", "VPS stock", "Real news: stock is back")]


def test_a_later_turn_does_not_strand_a_scheduled_run(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path)
    other = _admit(store, text="other", scheduled_task={"id": "t2", "title": "Other"}, message="other")
    store.transition_execution(other.execution_id, expected_version=other.status_version,
                               target=ExecutionStatus.FAILED, reason_code="test")
    paused = _lose_owner(driver, active, store)
    continued: list = []

    async def request_continue(**arguments):
        continued.append(arguments["execution_id"])

    monkeypatch.setattr(driver.control_service, "request_continue", request_continue)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert continued == [paused.execution_id]
    assert store.get_execution(paused.execution_id).status is ExecutionStatus.PAUSED


def test_a_later_turn_still_supersedes_a_chat_turn(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None)
    other = _admit(store, text="other", scheduled_task=None, message="other")
    store.transition_execution(other.execution_id, expected_version=other.status_version,
                               target=ExecutionStatus.FAILED, reason_code="test")
    _lose_owner(driver, active, store)
    continued: list = []

    async def request_continue(**arguments):
        continued.append(arguments["execution_id"])

    monkeypatch.setattr(driver.control_service, "request_continue", request_continue)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert continued == []


def _started_later(store, *, speaker, message):
    other = _admit(store, text=message, scheduled_task=None, message=message, speaker=speaker)
    store.transition_execution(other.execution_id, expected_version=other.status_version,
                               target=ExecutionStatus.FAILED, reason_code="test")


def _record_continues(driver, monkeypatch):
    continued: list = []

    async def request_continue(**arguments):
        continued.append(arguments["execution_id"])

    monkeypatch.setattr(driver.control_service, "request_continue", request_continue)
    return continued


def test_a_check_in_or_scheduled_run_does_not_supersede_a_paused_chat(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None)
    _started_later(store, speaker=CHECK_IN, message="look")
    _started_later(store, speaker="runtime/scheduler", message="task")
    paused = _lose_owner(driver, active, store)
    continued = _record_continues(driver, monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert continued == [paused.execution_id]


def test_what_a_scheduled_run_started_does_not_supersede_a_paused_chat(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None)
    scheduled = _admit(store, text="task", scheduled_task=None, message="task", speaker="runtime/scheduler")
    child = _admit(store, text="part", scheduled_task=None, message="part", parent=scheduled.execution_id)
    for execution in (child, scheduled):
        store.transition_execution(execution.execution_id, expected_version=execution.status_version,
                                   target=ExecutionStatus.FAILED, reason_code="test")
    paused = _lose_owner(driver, active, store)
    continued = _record_continues(driver, monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert continued == [paused.execution_id]


def _new_process(tmp_path, monkeypatch):
    """Everything held in memory is gone; only the log is left."""
    monkeypatch.setattr(run_tables, "_tables_by_root", weakref.WeakValueDictionary())
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    attempts = AttemptStore(store)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    return AgentProductionDriver(store, control_service=service, run_ended=run_finish.finish_resumed), store


def _pause_for_shutdown(driver, active, store, monkeypatch):
    """An orderly restart: the turn reached a safe point under the shutdown's pause."""
    restart.prepare_shutdown(_restart_runner(driver, store, monkeypatch))
    pausing = store.get_execution(active.execution_id)
    assert pausing.status is ExecutionStatus.PAUSING
    driver.control_service.arrive_safe_point(
        attempt_id=active.attempt_id, generation=active.generation,
        command_id=f"worker-restart:pause:{active.attempt_id}",
        expected_execution_version=pausing.status_version,
        fragment=CheckpointFragment(safe_point_kind="action.after",
                                    frontier=({"step_id": "next", "phase": "before"},), state_refs={"cursor": 1}),
    )
    assert store.get_execution(active.execution_id).status is ExecutionStatus.PAUSED


def _intents(store, execution_id):
    return [event for event in store.list_events(execution_id) if event.kind == restart._REQUEST]


def _forget_intents(store, execution_id):
    """The state an older version left: paused, its intent lost with its process."""
    with store._transaction() as connection:
        connection.delete("execution_events", execution_id=execution_id, kind=restart._REQUEST)


def test_a_restart_intent_survives_the_process_and_the_turn_continues(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None)
    _lose_owner(driver, active, store)
    sequence = _intents(store, active.execution_id)[0].sequence
    logged = [line["r"]["kind"] for rows in store.tables.log.read(SESSION) for line in rows
              if line.get("t") == "execution_events"]
    assert logged == [restart._REQUEST]  # The live feed stays out of the log.
    driver, store = _new_process(tmp_path, monkeypatch)
    assert _intents(store, active.execution_id)[0].sequence == sequence
    continued = _record_continues(driver, monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert continued == [active.execution_id]


def test_a_settled_intent_stays_settled_in_the_next_process(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path)
    _lose_owner(driver, active, store)
    restart.reconcile(_restart_runner(driver, store, monkeypatch, window=0))
    driver, store = _new_process(tmp_path, monkeypatch)
    continued = _record_continues(driver, monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert continued == []
    assert store.get_execution(active.execution_id).status is ExecutionStatus.INTERRUPTED
    assert [e.kind for e in store.list_events(active.execution_id)
            if e.kind in {restart._REQUEST, restart._SETTLED}] == [restart._REQUEST, restart._SETTLED]


@pytest.mark.parametrize("stop", ["crash", "orderly"])
@pytest.mark.parametrize("scheduled_task,speaker", [(TASK, "runtime/scheduler"), (None, CHECK_IN)])
def test_a_run_nobody_can_continue_ends_when_its_intent_was_lost(
        world, tmp_path, monkeypatch, stop, scheduled_task, speaker):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=scheduled_task, speaker=speaker)
    if stop == "crash":
        _lose_owner(driver, active, store)
    else:
        _pause_for_shutdown(driver, active, store, monkeypatch)
    _forget_intents(store, active.execution_id)
    continued = _record_continues(driver, monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    ended = store.get_execution(active.execution_id)
    assert ended.status is ExecutionStatus.INTERRUPTED and ended.reason_code == "restart_not_continued"
    assert continued == []
    _settled_by_events(store)
    assert _runs(status_only=True) == ([task_runs.FAILED] if scheduled_task else [])


def test_a_chat_whose_intent_was_lost_stays_paused(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None)
    _lose_owner(driver, active, store)
    _forget_intents(store, active.execution_id)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert store.get_execution(active.execution_id).status is ExecutionStatus.PAUSED


@pytest.mark.parametrize("stop", ["crash", "orderly"])
def test_a_check_in_cut_by_a_restart_ends_and_is_never_continued(world, tmp_path, monkeypatch, stop):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, speaker=CHECK_IN)
    if stop == "crash":
        _lose_owner(driver, active, store)
    else:
        _pause_for_shutdown(driver, active, store, monkeypatch)
        assert restart.stopped_by_a_restart(store, store.get_execution(active.execution_id))
    driver, store = _new_process(tmp_path, monkeypatch)
    continued = _record_continues(driver, monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    ended = store.get_execution(active.execution_id)
    assert ended.status is ExecutionStatus.INTERRUPTED and ended.reason_code == "restart_not_continued"
    assert continued == []


def _die_before_the_safe_point(driver, active, store, monkeypatch):
    """An orderly restart that ran out of time: the web turn was not at a safe point yet."""
    restart.prepare_shutdown(_restart_runner(driver, store, monkeypatch))
    assert store.get_execution(active.execution_id).status is ExecutionStatus.PAUSING
    driver.control_service.recover_owner_loss(active.execution_id, attempt_id=active.attempt_id,
                                              generation=active.generation)
    assert store.get_execution(active.execution_id).status is ExecutionStatus.INTERRUPTED


def _record_new_turns(monkeypatch):
    started: list = []

    def start(_store, execution, _event):
        started.append(execution.execution_id)
        return "new_turn_admitted"

    monkeypatch.setattr(hooks, "start_recovery_turn", start)
    return started


def _unanswered(store, execution_id):
    return [event for event in _intents(store, execution_id) if not any(
        settled.kind == restart._SETTLED and settled.payload.get("request_sequence") == event.sequence
        for settled in store.list_events(execution_id))]


def test_a_check_in_that_died_before_a_safe_point_is_not_started_over(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, speaker=CHECK_IN)
    _die_before_the_safe_point(driver, active, store, monkeypatch)
    started = _record_new_turns(monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert started == [] and _unanswered(store, active.execution_id) == []


def test_a_migrated_handoff_waits_while_the_restart_settles_its_conversation(world, tmp_path, monkeypatch):
    from openprogram.runtime.conversation import owner_turn
    from openprogram.runtime.conversation.always_on import handoff_migration
    from openprogram.runtime.execution import store as execution_store
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, source="web")
    _die_before_the_safe_point(driver, active, store, monkeypatch)
    session_id = store.get_execution(active.execution_id).session_id
    monkeypatch.setattr(execution_store, "default_store", lambda: store)
    monkeypatch.setattr(owner_turn, "WAKE_POLL_S", 0.01)
    monkeypatch.setattr(owner_turn, "WAKE_WAIT_S", 5.0)
    checks = []
    real = restart.conversation_settling

    def settling(*args):
        checks.append(real(*args))
        if len(checks) == 2:
            monkeypatch.setattr(hooks, "start_recovery_turn", lambda *_a: "expired")
            restart.reconcile(_restart_runner(driver, store, monkeypatch))
        return checks[-1]

    monkeypatch.setattr(restart, "conversation_settling", settling)
    handoff_migration.wait_for_restart_to_settle(session_id)
    assert checks[:2] == [True, True] and checks[-1] is False


def test_a_new_turn_that_cannot_start_reports_nothing_continues(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, source="web")
    _die_before_the_safe_point(driver, active, store, monkeypatch)
    session_id = store.get_execution(active.execution_id).session_id
    assert restart.conversation_settling(store, session_id)
    abandoned: list = []
    monkeypatch.setattr(hooks, "restart_abandoned", lambda _store, execution: abandoned.append(execution.execution_id))
    monkeypatch.setattr(hooks, "start_recovery_turn", lambda *_a: "expired")
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert abandoned == [active.execution_id] and _unanswered(store, active.execution_id) == []
    assert not restart.conversation_settling(store, session_id)


def test_a_new_turn_that_starts_reports_nothing(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, source="web")
    _die_before_the_safe_point(driver, active, store, monkeypatch)
    monkeypatch.setattr(hooks, "restart_abandoned", lambda *_a: pytest.fail("it continues"))
    _record_new_turns(monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))


def test_a_new_turn_a_restart_owes_is_read_at_the_next_start(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, source="web")
    _die_before_the_safe_point(driver, active, store, monkeypatch)
    driver, store = _new_process(tmp_path, monkeypatch)
    started = _record_new_turns(monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert started == [active.execution_id]
    assert store.tables.log.active_sessions() == []  # Answered: nothing to read at the next start.


def test_a_conversation_owed_an_answer_stays_in_memory_until_it_gets_one(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, source="web")
    _die_before_the_safe_point(driver, active, store, monkeypatch)
    later = store.get_execution(active.execution_id).updated_at + 100
    assert store.tables.forget_finished_sessions(1, now=later) == 0
    _record_new_turns(monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert store.tables.forget_finished_sessions(1, now=later) == 1


def test_an_intent_the_next_recovery_wrote_over_is_answered(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None)
    with store._transaction() as connection:
        restart.record_intent(store, connection, store.get_execution(active.execution_id), interrupted_at=1.0,
                              seconds=-1, pause_command_id="worker-restart:pause:never-accepted")
    _lose_owner(driver, active, store)
    first, second = _intents(store, active.execution_id)
    _record_continues(driver, monkeypatch)
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert [event.sequence for event in _unanswered(store, active.execution_id)] == [second.sequence]
    assert [event.payload["outcome"] for event in store.list_events(active.execution_id)
            if event.kind == restart._SETTLED and event.payload["request_sequence"] == first.sequence] == ["replaced"]


def test_an_intent_of_a_turn_that_ended_is_answered(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None)
    paused = _lose_owner(driver, active, store)
    store.transition_execution(paused.execution_id, expected_version=paused.status_version,
                               target=ExecutionStatus.INTERRUPTED, reason_code="test")
    restart.reconcile(_restart_runner(driver, store, monkeypatch))
    assert [e.payload["outcome"] for e in store.list_events(active.execution_id)
            if e.kind == restart._SETTLED] == ["execution_ended"]


def _settled_by_events(store):
    ExecutionEventEffects(store, after=0).drain()


def test_a_paused_run_the_restart_will_not_continue_ends_as_a_failure(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path)
    _lose_owner(driver, active, store)
    restart.reconcile(_restart_runner(driver, store, monkeypatch, window=0))
    assert store.get_execution(active.execution_id).status is ExecutionStatus.INTERRUPTED
    _settled_by_events(store)
    _settled_by_events(store)
    assert _runs() == [(task_runs.FAILED, "the run was interrupted and could not continue")]
    assert world.reported == []


def test_a_rejected_continue_ends_a_scheduled_run(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path)
    _lose_owner(driver, active, store)

    async def incompatible(*_arguments):
        raise RuntimeContractError("continuation_contract_mismatch", "saved runtime differs")

    driver.control_service.activator = incompatible
    runner = _restart_runner(driver, store, monkeypatch)
    for _ in range(3):
        restart.reconcile(runner)
    assert store.get_execution(active.execution_id).status is ExecutionStatus.INTERRUPTED
    _settled_by_events(store)
    assert _runs(status_only=True) == [task_runs.FAILED]


def test_a_chat_turn_the_restart_gave_up_on_stays_paused(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None)
    _lose_owner(driver, active, store)
    restart.reconcile(_restart_runner(driver, store, monkeypatch, window=0))
    assert store.get_execution(active.execution_id).status is ExecutionStatus.PAUSED


@pytest.mark.parametrize("target,reason", [(ExecutionStatus.INTERRUPTED, "owner_lost"),
                                           (ExecutionStatus.FAILED, "wait_timeout"),
                                           (ExecutionStatus.FAILED, "wait_declined")])
def test_an_ending_no_turn_reports_is_still_recorded(world, tmp_path, target, reason):
    driver, active, store = _ran_turn(tmp_path)
    paused = _lose_owner(driver, active, store)
    store.transition_execution(paused.execution_id, expected_version=paused.status_version,
                               target=target, reason_code=reason)
    _settled_by_events(store)
    assert _runs(status_only=True) == [task_runs.FAILED]


def test_a_failure_the_turn_reports_itself_is_not_counted_twice(world, tmp_path):
    driver, active, store = _ran_turn(tmp_path)
    driver._finish_attempt(active, FAILURE, threading.Event())
    _settled_by_events(store)
    assert _runs() == [(task_runs.FAILED, "the page would not open")]


def test_an_ordinary_turn_is_nobodys_scheduled_run(world, tmp_path):
    driver, active, _store = _ran_turn(tmp_path, scheduled_task=None)
    driver._finish_attempt(active, _reply("hello"), threading.Event())
    assert task_runs.recent("t1") == [] and world.reported == []


def test_the_worker_continues_a_turn_with_the_same_finish():
    from openprogram.runtime.conversation import execution_hooks

    activate = execution_hooks._agent_activator(ExecutionStore.__new__(ExecutionStore), None)
    assert activate.__self__.run_ended is execution_hooks._run_ended


def test_a_resumed_run_the_original_process_also_finished_tells_the_user_once(world, tmp_path):
    driver, active, _store = _ran_turn(tmp_path)
    reply = _reply("In stock")
    outcome = run_finish.conclude(SESSION, TASK, active.execution_id, reply, COMPLETED)
    run_finish.keep(outcome)
    run_finish.finish(outcome)
    driver._finish_attempt(active, reply, threading.Event())
    run_finish.finish(outcome)
    run_finish.finish_kept()
    assert len(task_runs.recent("t1")) == 1 and len(world.reported) == 1
    assert _kept(world) == []


def test_two_runs_of_a_task_are_two_runs(world):
    for execution_id in ("run-1", "run-2"):
        run_finish.finish(run_finish.conclude(SESSION, TASK, execution_id, _reply("news"), COMPLETED))
    assert len(task_runs.recent("t1")) == 2 and len(world.reported) == 2


def test_a_run_that_outlived_its_worker_is_finished_by_the_next_one(world):
    outcome = run_finish.conclude(SESSION, TASK, "run-1", _reply("In stock"), COMPLETED)
    run_finish.keep(outcome)
    assert worker._tick({}) == 0
    assert [run["status"] for run in task_runs.recent("t1")] == [task_runs.RESULT]
    assert world.reported == [(SESSION, "t1", "VPS stock", "In stock")]
    assert _kept(world) == []
    worker._tick({})
    assert len(world.reported) == 1


def test_a_resumed_run_settles_the_tasks_branch(world, tmp_path):
    placement = task_branch.prepare(SESSION, TASK, world.db)
    tip = f"{placement.msg_id}_reply"
    world.db.append_message(SESSION, {"id": placement.msg_id, "role": "user", "content": "check",
                                      "source": "scheduler", "predecessor": placement.parent, "timestamp": 1.0})
    world.db.append_message(SESSION, {"id": tip, "role": "assistant", "content": "news", "source": "scheduler",
                                      "predecessor": placement.msg_id, "timestamp": 2.0})
    world.db.set_head(SESSION, "a0")
    driver, active, _store = _ran_turn(tmp_path)
    driver._finish_attempt(active, _reply("news", tip=tip), threading.Event())
    assert task_branch.bound_tip(SESSION, "t1", world.db) == tip


# --- firings that never became a turn ---------------------------------------


def test_skips_leave_the_failure_count_alone_and_share_one_row(world):
    task_runs.record("t1", task_runs.FAILED, "403")
    for _ in range(50):
        assert task_runs.record("t1", task_runs.SKIPPED, "busy") == 1
    runs = task_runs.recent("t1")
    assert [run["status"] for run in runs] == [task_runs.SKIPPED, task_runs.FAILED]
    assert runs[0]["count"] == 50
    assert task_runs.record("t1", task_runs.FAILED, "403") == 2


def test_a_skip_after_the_third_failure_does_not_repeat_the_notice(world):
    for _ in range(task_runs.FAILURE_NOTICE):
        run_finish.finish({"session_id": SESSION, "task_id": "t1", "title": "VPS stock",
                           "status": task_runs.FAILED, "text": "403"})
    assert len(world.reported) == 1
    run_finish.finish({"session_id": SESSION, "task_id": "t1", "title": "VPS stock",
                       "status": task_runs.SKIPPED, "text": "busy"})
    assert len(world.reported) == 1


class _StartingAdapter:
    """The adapter a scheduled run starts through, up to the point under test."""

    def __init__(self, status=ExecutionStatus.QUEUED, attempt=None, admit_error=None, activate_error=None,
                 activated=None):
        self.execution = SimpleNamespace(status=status, current_attempt_id=attempt)
        self.admit_error, self.activate_error, self.activated = admit_error, activate_error, activated
        self.failed: list[str] = []
        self.store = SimpleNamespace(get_execution=lambda _id: self.execution)

    def admit(self, *_arguments, **_keywords):
        if self.admit_error:
            raise self.admit_error
        return SimpleNamespace(execution_id="execution-1")

    async def activate(self, _admission):
        if self.activate_error:
            raise self.activate_error
        return self.activated

    def fail_admission(self, _admission, *, reason_code, failure_text=None):
        self.failed.append(reason_code)
        self.execution = SimpleNamespace(status=ExecutionStatus.FAILED, current_attempt_id=None)


def _start(world, monkeypatch, adapter):
    from openprogram.runtime.conversation import production_driver
    from openprogram.runtime.permissions import rule

    spec = {"session_id": SESSION, "agent_id": "main", "prompt": "check",
            "job_authority": {"principal_id": "owner", "authority_tier": "owner"}}
    monkeypatch.setattr(worker, "_verify_execution_spec", lambda value: (value, None))
    monkeypatch.setattr(worker, "_conversation_settings", lambda _session, _branch_id=None: ("ask", None))
    monkeypatch.setattr(worker._sandbox, "resolve_policy", lambda **_keywords: None)
    monkeypatch.setattr(worker._sandbox, "install_policy_snapshot", lambda _snapshot: None)
    monkeypatch.setattr(rule, "load_merged_rules", lambda _session: [])
    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", lambda: adapter)
    return worker._run_prompt_turn(spec, str(world.root / "run.log"), None, "", dict(TASK))


def test_a_run_that_could_not_be_admitted_is_one_failed_run(world, monkeypatch):
    outcome = _start(world, monkeypatch, _StartingAdapter(admit_error=RuntimeError("disk full")))
    assert outcome["status"] == task_runs.FAILED and "disk full" in outcome["text"]
    run_finish.keep(outcome)
    run_finish.finish(outcome)
    assert [run["status"] for run in task_runs.recent("t1")] == [task_runs.FAILED]
    assert _kept(world) == []


def test_a_run_whose_lease_failed_ends_and_counts_once(world, monkeypatch):
    adapter = _StartingAdapter(activate_error=RuntimeError("lease refused"))
    outcome = _start(world, monkeypatch, adapter)
    assert adapter.failed == ["scheduled_run_start_failed"]
    assert outcome["execution_id"] == "execution-1" and "lease refused" in outcome["text"]
    run_finish.finish(outcome)
    assert task_runs.record("t1", task_runs.FAILED, "again") == 2


def test_a_pause_that_wins_before_the_start_is_no_failure(world, monkeypatch):
    assert _start(world, monkeypatch, _StartingAdapter(activated=None)) is None
    assert "TypeError" not in (world.root / "run.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("status", [ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED])
def test_a_broken_start_records_nothing_for_a_run_still_owned_or_stopped(world, monkeypatch, status):
    adapter = _StartingAdapter(status=status, attempt="attempt", activate_error=RuntimeError("lost"))
    assert _start(world, monkeypatch, adapter) is None
    assert adapter.failed == []


# --- archived conversations, restarts and one-time tasks --------------------


def test_a_task_in_an_archived_conversation_starts_no_turn(world, monkeypatch):
    world.db.update_session(SESSION, archived=True)
    prepared, adapter = [], _StartingAdapter()
    monkeypatch.setattr(task_branch, "prepare", lambda *arguments: prepared.append(arguments))
    monkeypatch.setattr(adapter, "admit", lambda *_arguments, **_keywords: pytest.fail("a turn was requested"))
    outcome = _start(world, monkeypatch, adapter)
    assert prepared == [] and outcome["status"] == task_runs.SKIPPED
    assert "# skipped" in (world.root / "run.log").read_text(encoding="utf-8")
    run_finish.finish(outcome)
    assert [run["status"] for run in task_runs.recent("t1")] == [task_runs.SKIPPED]
    assert world.reported == []


def test_an_unfinished_run_from_an_earlier_worker_blocks_the_next(world, tmp_path, monkeypatch):
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    monkeypatch.setattr(execution_package, "default_store", lambda: store)
    worker._RUNNING.pop("t1", None)
    assert worker._previous_run_active("t1", SESSION) is False
    _admit(store)
    assert worker._previous_run_active("t1", SESSION) is True
    assert worker._previous_run_active("another", SESSION) is False


def _run_held_by(store, monkeypatch, owner):
    execution = _admit(store)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(execution.execution_id, expected_version=execution.status_version,
                                      owner_id="worker", attempt_id="attempt")
    attempts.activate(leased.attempt_id, generation=leased.generation,
                      expected_execution_version=reserved.status_version)
    monkeypatch.setattr(process_owner, "current_process_owner", lambda: owner)
    attempts.set_process_owner(leased.attempt_id, generation=leased.generation, active=True)


def _earlier_child(*, alive):
    pid = os.getppid()
    return {"instance": "earlier-child", "pid": pid, "started": process_start_token(pid) if alive else "ended"}


@pytest.mark.parametrize("alive", [True, False])
def test_only_a_run_whose_process_still_exists_blocks_the_next(world, tmp_path, monkeypatch, alive):
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    monkeypatch.setattr(execution_package, "default_store", lambda: store)
    worker._RUNNING.pop("t1", None)
    _run_held_by(store, monkeypatch, _earlier_child(alive=alive))
    assert worker._previous_run_active("t1", SESSION) is alive


ONCE = {"id": "t1", "type": "once", "run_at": "2026-11-09T01:00:00+00:00"}
AFTER = datetime.datetime(2026, 11, 9, 2, 0, tzinfo=datetime.timezone.utc)


def _on_file(world, **changes):
    cron._save(str(world.root / "tasks.json"), [{**ONCE, **changes}])


def _claimed(world, entry=ONCE):
    return worker._claim_entry(entry, AFTER, reboot=False)


def test_a_fired_one_time_task_fires_again_only_when_its_time_moves(world):
    _on_file(world)
    assert _claimed(world) is not None
    assert _claimed(world) is None
    worker.forget_firing("t1")
    assert _claimed(world) is not None


def test_a_claim_for_a_time_since_edited_away_is_refused(world):
    _on_file(world, run_at="2026-11-09T03:00:00+00:00")
    assert _claimed(world) is None


def _claim_left_by(owner):
    worker._save_state({"t1": {"last_fired_minute": "2026-11-09T01:00", "status": "starting", "owner": owner}})


def test_a_start_cut_by_a_crash_fires_but_a_live_start_does_not(world):
    _on_file(world)
    token, *_rest = _claimed(world)
    assert token and _claimed(world) is None  # this process is still starting it
    _claim_left_by(_earlier_child(alive=True))
    assert _claimed(world) is None
    _claim_left_by(_earlier_child(alive=False))
    assert _claimed(world) is not None


def test_a_worker_back_under_the_same_process_number_still_fires_a_cut_start(world):
    _on_file(world)
    _claim_left_by({"instance": "earlier-worker", "pid": os.getpid(), "started": "earlier"})
    assert _claimed(world) is not None


def test_a_start_with_no_owner_on_record_stays_spent(world):
    _on_file(world)
    worker._save_state({"t1": {"last_fired_minute": "2026-11-09T01:00", "status": "starting"}})
    assert _claimed(world) is None


def test_moving_a_fired_one_time_task_clears_its_firing(world, tmp_path):
    task = scheduler_service.create_task(
        title="Once", task_type="once", command="echo hi", run_at="2026-11-09T01:00:00+00:00", cwd=str(tmp_path),
    )
    worker._save_state({task["id"]: {"last_fired_minute": "2026-11-09T01:00"}})
    scheduler_service.update_task(task["id"], {"title": "Renamed"})
    assert task["id"] in worker._load_state()
    # The editor sends the same time back in UTC, and without its seconds.
    scheduler_service.update_task(task["id"], {"run_at": "2026-11-09T09:00:00.000+08:00"})
    assert task["id"] in worker._load_state()
    scheduler_service.update_task(task["id"], {"run_at": "2026-11-09T05:00:00+00:00"})
    assert task["id"] not in worker._load_state()


def _report_raises(world, monkeypatch, failing_calls=None):
    """``report_scheduled`` raises for the first ``failing_calls`` calls, or always."""
    from openprogram.runtime.conversation.always_on import runtime as always_on

    calls: list[tuple] = []

    def report(*arguments, **_files):
        calls.append(arguments)
        if failing_calls is None or len(calls) <= failing_calls:
            raise RuntimeError("store unavailable")
        world.reported.append(arguments)

    monkeypatch.setattr(always_on, "report_scheduled", report)
    return calls


def test_a_report_that_raises_is_told_on_the_next_tick(world, monkeypatch):
    calls = _report_raises(world, monkeypatch, failing_calls=1)
    run_finish.finish(run_finish.conclude(SESSION, TASK, "run-1", _reply("In stock"), COMPLETED))
    assert task_runs.recent("t1") == [] and _kept(world) == ["run-1.json"]

    run_finish.finish_kept()
    assert len(calls) == 2 and len(world.reported) == 1
    assert len(task_runs.recent("t1")) == 1 and _kept(world) == []


def test_a_report_that_keeps_raising_stops_at_five_and_keeps_the_row(world, monkeypatch):
    calls = _report_raises(world, monkeypatch)
    run_finish.finish(run_finish.conclude(SESSION, TASK, "run-1", _reply("In stock"), COMPLETED))
    for _ in range(9):
        run_finish.finish_kept()
    assert len(calls) == run_finish.ANNOUNCE_ATTEMPTS == 5
    assert [run["status"] for run in task_runs.recent("t1")] == [task_runs.RESULT] and _kept(world) == []


def test_a_retried_failure_notice_uses_the_count_of_its_own_run(world, monkeypatch):
    task_runs.record("t1", task_runs.FAILED, "broke", execution_id="earlier-1")
    task_runs.record("t1", task_runs.FAILED, "broke", execution_id="earlier-2")
    _report_raises(world, monkeypatch, failing_calls=1)
    run_finish.finish(run_finish.conclude(SESSION, TASK, "run-3", FAILURE, COMPLETED))
    task_runs.record("t1", task_runs.FAILED, "broke", execution_id="later")

    run_finish.finish_kept()
    assert len(world.reported) == 1 and "3" in world.reported[0][3]


def test_a_row_that_cannot_be_written_does_not_tell_the_user_twice(world, monkeypatch):
    real_record, failed = task_runs.record, []

    def record(*arguments, **options):
        if not failed:
            failed.append(True)
            raise OSError("disk full")
        return real_record(*arguments, **options)

    monkeypatch.setattr(task_runs, "record", record)
    run_finish.finish(run_finish.conclude(SESSION, TASK, "run-1", _reply("In stock"), COMPLETED))
    run_finish.finish_kept()
    assert len(world.reported) == 1 and len(task_runs.recent("t1")) == 1 and _kept(world) == []


def test_two_paths_racing_on_one_ending_leave_one_row_and_one_report(world):
    outcome = run_finish.conclude(SESSION, TASK, "run-1", _reply("In stock"), COMPLETED)
    run_finish.keep(outcome)
    start = threading.Barrier(8)

    def finish():
        start.wait()
        run_finish.finish(outcome)

    threads = [threading.Thread(target=finish) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(task_runs.recent("t1")) == 1 and len(world.reported) == 1 and _kept(world) == []


def test_two_writers_of_one_execution_add_one_row(world):
    start = threading.Barrier(8)
    answers: list = []

    def record():
        start.wait()
        answers.append(task_runs.record("t1", task_runs.RESULT, "news", execution_id="run-1"))

    threads = [threading.Thread(target=record) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(task_runs.recent("t1")) == 1 and answers.count(None) == 7


def test_a_failure_notice_of_a_firing_no_turn_ran_is_retried(world, monkeypatch):
    task_runs.record("t1", task_runs.FAILED, "exit 1")
    task_runs.record("t1", task_runs.FAILED, "exit 1")
    _report_raises(world, monkeypatch, failing_calls=1)
    firing = {"session_id": SESSION, "task_id": "t1", "title": "VPS stock", "status": task_runs.FAILED, "text": "exit 1"}
    run_finish.finish(firing)
    assert len(task_runs.recent("t1")) == 2 and len(_kept(world)) == 1

    run_finish.finish_kept()
    assert len(world.reported) == 1 and "3" in world.reported[0][3]
    assert len(task_runs.recent("t1")) == 3 and _kept(world) == []


def test_clients_refresh_once_the_row_exists(world, monkeypatch):
    from openprogram.runtime import interface_link

    frames: list[tuple] = []
    monkeypatch.setattr(interface_link, "broadcast", lambda frame: frames.append((frame, len(task_runs.recent("t1")))))
    run_finish.finish(run_finish.conclude(SESSION, TASK, "run-1", _reply("In stock"), COMPLETED))
    assert frames == [({"type": "always_on_update", "data": {"session_id": SESSION}}, 1)]


@pytest.mark.parametrize("always_on_agent, status", [("butler", task_runs.QUIET), (None, task_runs.RESULT)])
def test_a_progress_line_before_the_quiet_mark_stays_quiet_for_an_always_on_agent(world, monkeypatch, always_on_agent, status):
    from openprogram.runtime.conversation import always_on

    monkeypatch.setattr(always_on, "agent_for_session", lambda _sid: always_on_agent)
    result = TurnResult(final_text="Checking.[nothing new]", user_msg_id="u", assistant_msg_id="a",
                        last_round_text="[nothing new]")
    outcome = run_finish.conclude(SESSION, dict(TASK), "e1", result, COMPLETED)
    assert outcome["status"] == status


def test_a_failed_check_after_a_progress_line_counts_as_unable(world, monkeypatch):
    from openprogram.runtime.conversation import always_on

    monkeypatch.setattr(always_on, "agent_for_session", lambda _sid: "butler")
    result = TurnResult(final_text="Checking.[could not check] The page timed out.", user_msg_id="u",
                        assistant_msg_id="a", last_round_text="[could not check] The page timed out.")
    outcome = run_finish.conclude(SESSION, dict(TASK), "e1", result, COMPLETED)
    assert outcome["status"] == task_runs.FAILED and outcome["text"] == "The page timed out."


def test_a_run_that_sent_a_file_is_a_result_and_carries_it(world, monkeypatch):
    from openprogram.runtime.conversation import always_on

    monkeypatch.setattr(always_on, "agent_for_session", lambda _sid: "butler")
    result = TurnResult(final_text="[nothing new]\n[attachment]", user_msg_id="u", assistant_msg_id="a",
                        last_round_text="[nothing new]", attached_files=["/tmp/a.txt"])
    outcome = run_finish.conclude(SESSION, dict(TASK), "e1", result, COMPLETED)
    assert outcome["status"] == task_runs.RESULT and outcome["attached_files"] == ["/tmp/a.txt"]
    assert outcome["text"] == "[attachment]"


# ── Time limits ──────────────────────────────────────────────────────


def _limits_see(driver, store, monkeypatch):
    monkeypatch.setattr(execution_package, "default_store", lambda: store)
    monkeypatch.setattr(execution_package, "default_control_service", lambda: driver.control_service)


def _past_limit(store, execution_id, limit, by=1.0):
    return store.get_execution(execution_id).created_at + limit + by


def test_a_run_inside_its_limit_is_left_alone(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    assert time_limits.stop_overdue(_past_limit(store, active.execution_id, time_limits.SCHEDULED_RUN_LIMIT_S, -1.0)) == []
    assert store.get_execution(active.execution_id).status is ExecutionStatus.RUNNING


def test_a_scheduled_run_past_its_limit_stops_and_says_why(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    now = _past_limit(store, active.execution_id, time_limits.SCHEDULED_RUN_LIMIT_S)
    assert time_limits.stop_overdue(now) == [active.execution_id]
    stopping = store.get_execution(active.execution_id)
    assert stopping.status is ExecutionStatus.CANCELLING and stopping.reason_code == time_limits.TIME_LIMIT
    event = threading.Event()
    event.set()
    driver._finish_attempt(active, _reply("half done"), event)
    assert store.get_execution(active.execution_id).status is ExecutionStatus.CANCELLED
    assert _runs() == [(task_runs.FAILED, time_limits.SCHEDULED_RUN_STOPPED)]


def test_a_resumed_run_counts_its_earlier_time(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    resumed = _next_attempt(store, _lose_owner(driver, active, store))
    _limits_see(driver, store, monkeypatch)
    now = _past_limit(store, active.execution_id, time_limits.SCHEDULED_RUN_LIMIT_S)
    assert time_limits.stop_overdue(now) == [active.execution_id]
    current = store.get_execution(active.execution_id)
    assert current.status is ExecutionStatus.CANCELLING and current.current_attempt_id == resumed.attempt_id


def test_a_paused_run_past_its_limit_ends_with_its_reason(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _lose_owner(driver, active, store)
    _limits_see(driver, store, monkeypatch)
    time_limits.stop_overdue(_past_limit(store, active.execution_id, time_limits.SCHEDULED_RUN_LIMIT_S))
    assert store.get_execution(active.execution_id).status is ExecutionStatus.CANCELLED
    assert _runs() == [(task_runs.FAILED, time_limits.SCHEDULED_RUN_STOPPED)]


def test_a_paused_run_stopped_by_something_else_first_is_not_finished_again(world, tmp_path, monkeypatch):
    """Listed past its limit, then stopped elsewhere before the limit's stop:
    the limit's stop answers already_terminal and reports nothing."""
    import contextlib

    from openprogram.runtime.execution.control import ProjectionRecoveryRequired

    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    paused = _lose_owner(driver, active, store)
    _limits_see(driver, store, monkeypatch)
    # A Stop from the page, under its own command id. This world has no
    # reply row for the stopped turn to project into, which is not the point.
    with contextlib.suppress(ProjectionRecoveryRequired):
        asyncio.run(driver.control_service.request_cancel(
            command_id="page-stop", execution_id=paused.execution_id,
            actor={"source": "web"}, reason_code="cancel.user",
        ))
    assert store.get_execution(paused.execution_id).status is ExecutionStatus.CANCELLED
    finished = []
    monkeypatch.setattr(run_finish, "finish_resumed", lambda *args: finished.append(args))

    time_limits._stop(store, paused)

    assert finished == []


def test_a_check_in_has_the_shorter_limit(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, speaker=CHECK_IN)
    _limits_see(driver, store, monkeypatch)
    time_limits.stop_overdue(_past_limit(store, active.execution_id, time_limits.CHECK_IN_LIMIT_S))
    assert store.get_execution(active.execution_id).reason_code == time_limits.TIME_LIMIT


def test_a_turn_someone_is_attending_has_no_limit(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, scheduled_task=None, source="web")
    _limits_see(driver, store, monkeypatch)
    assert time_limits.stop_overdue(store.get_execution(active.execution_id).created_at + 10 * 86400) == []
    assert store.get_execution(active.execution_id).status is ExecutionStatus.RUNNING


def test_a_run_in_its_own_live_process_keeps_its_own_clock(world, tmp_path, monkeypatch):
    from openprogram.runtime.execution import process_owner

    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    monkeypatch.setattr(process_owner, "owned_by_another_process", lambda _lease: True)
    monkeypatch.setattr(process_owner, "process_owner_may_be_alive", lambda _lease: True)
    assert time_limits.stop_overdue(_past_limit(store, active.execution_id, time_limits.SCHEDULED_RUN_LIMIT_S)) == []


@pytest.mark.parametrize("alive", [True, False])
def test_a_run_whose_own_process_exited_ends_at_the_next_tick(world, tmp_path, monkeypatch, alive):
    """Killed with its process, well inside its limit: it ends as a failed run, its
    pages close, and nothing is left paused for a restart to continue."""
    from openprogram.runtime.execution import process_owner
    from openprogram.state.store.session import session_log

    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    closed = []
    monkeypatch.setattr(run_finish, "close_run_tabs", lambda execution_id, **_: closed.append(execution_id))
    monkeypatch.setattr(session_log, "is_main_writer", lambda: True)
    monkeypatch.setattr(process_owner, "owned_by_another_process", lambda _lease: True)
    monkeypatch.setattr(process_owner, "process_owner_may_be_alive", lambda _lease: alive)
    ended = time_limits.stop_overdue(store.get_execution(active.execution_id).created_at + 60)
    current = store.get_execution(active.execution_id)
    if alive:
        assert ended == [] and current.status is ExecutionStatus.RUNNING and closed == []
        return
    assert ended == [active.execution_id]
    assert current.status is ExecutionStatus.INTERRUPTED and current.reason_code == "owner_lost"
    assert not restart._has_open_intent(store, current)
    assert closed == [active.execution_id]
    assert _runs() == [(task_runs.FAILED, "the run was interrupted and could not continue")]


def _commands_of(store, active, tmp_path, monkeypatch):
    """Three live commands: one the run waits on, one it left in the background
    and one of another run; ``control`` records what each is sent."""
    from openprogram.host import processes

    records = processes.ProcessStore(tmp_path / "processes.db")
    monkeypatch.setattr(processes, "ProcessStore", lambda: records)
    session_id = store.get_execution(active.execution_id).session_id

    def running(execution_id, *, background=False):
        record = records.create(session_id=session_id, execution_id=execution_id, tool_call_id="call",
                                command="sleep 120", cwd=str(tmp_path), backend_id="local")
        return records.update(record["id"], status="running", heartbeat=time.time(), background=background)["id"]

    ids = SimpleNamespace(waited_on=running(active.execution_id),
                          left_running=running(active.execution_id, background=True),
                          other_run=running("exec_other"), sent=[], records=records)
    monkeypatch.setattr(processes, "control",
                        lambda _store, process_id, kind, *_a, **_k: ids.sent.append((process_id, kind)))
    return ids


def _only_the_waited_on_command_stopped(commands):
    assert commands.sent == [(commands.waited_on, "stop")]
    assert commands.records.get(commands.waited_on)["reported"] is True
    assert {commands.records.get(commands.left_running)["reported"],
            commands.records.get(commands.other_run)["reported"]} == {False}


def test_a_run_whose_own_process_exited_stops_the_command_it_was_waiting_on(world, tmp_path, monkeypatch):
    """A command runs under a supervisor of its own and outlives the killed process:
    the one the run was waiting on stops with the ending, one it had left in the
    background goes on, and another run's command is not touched."""
    from openprogram.runtime.execution import process_owner
    from openprogram.state.store.session import session_log

    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    monkeypatch.setattr(run_finish, "close_run_tabs", lambda execution_id, **_: None)
    monkeypatch.setattr(session_log, "is_main_writer", lambda: True)
    monkeypatch.setattr(process_owner, "owned_by_another_process", lambda _lease: True)
    monkeypatch.setattr(process_owner, "process_owner_may_be_alive", lambda _lease: False)
    commands = _commands_of(store, active, tmp_path, monkeypatch)

    assert time_limits.end_runs_of_exited_processes() == [active.execution_id]
    _only_the_waited_on_command_stopped(commands)


def test_a_run_stopped_by_the_user_whose_process_then_died_ends_stopped(world, tmp_path, monkeypatch):
    from openprogram.runtime.execution import process_owner
    from openprogram.state.store.session import session_log

    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    _cancel(driver, active, store)
    closed = []
    monkeypatch.setattr(run_finish, "close_run_tabs", lambda execution_id, **_: closed.append(execution_id))
    monkeypatch.setattr(session_log, "is_main_writer", lambda: True)
    monkeypatch.setattr(process_owner, "owned_by_another_process", lambda _lease: True)
    monkeypatch.setattr(process_owner, "process_owner_may_be_alive", lambda _lease: False)
    assert time_limits.end_runs_of_exited_processes() == [active.execution_id]
    assert store.get_execution(active.execution_id).status is ExecutionStatus.CANCELLED
    assert closed == [active.execution_id] and _runs() == []


def test_a_worker_that_is_stopping_leaves_an_exited_run_to_the_next_one(world, tmp_path, monkeypatch):
    from openprogram.runtime.conversation import run_control
    from openprogram.runtime.execution import process_owner
    from openprogram.state.store.session import session_log

    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    monkeypatch.setattr(session_log, "is_main_writer", lambda: True)
    monkeypatch.setattr(run_control, "is_worker_stopping", lambda: True)
    monkeypatch.setattr(process_owner, "owned_by_another_process", lambda _lease: True)
    monkeypatch.setattr(process_owner, "process_owner_may_be_alive", lambda _lease: False)
    assert time_limits.end_runs_of_exited_processes() == []
    assert store.get_execution(active.execution_id).status is ExecutionStatus.RUNNING


def test_a_sub_agent_shares_the_clock_of_the_run_it_works_for(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    child = _admit(store, scheduled_task=None, message="child", parent=active.execution_id)
    _limits_see(driver, store, monkeypatch)
    stopped = time_limits.stop_overdue(_past_limit(store, active.execution_id, time_limits.SCHEDULED_RUN_LIMIT_S))
    assert set(stopped) == {active.execution_id, child.execution_id}


def test_the_process_of_a_run_stops_it_at_its_limit(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    monkeypatch.setitem(time_limits._LIMITS, "runtime/scheduler", 0.0)
    expired = threading.Event()
    real_stop = time_limits._stop

    def stop(*arguments):
        real_stop(*arguments)
        expired.set()

    monkeypatch.setattr(time_limits, "_stop", stop)
    time_limits.watch(active.execution_id)
    assert expired.wait(5)
    assert store.get_execution(active.execution_id).reason_code == time_limits.TIME_LIMIT


def _running_command(world, monkeypatch, *, started_at, proc=None, token="t0"):
    log = world.root / "run.log"
    log.write_text("", encoding="utf-8")
    proc = proc or SimpleNamespace(pid=4242, poll=lambda: None)
    monkeypatch.setattr(worker, "process_start_token", lambda _pid: token)
    monkeypatch.setattr(worker, "_resolve_path", cron._resolve_path)
    with monkeypatch.context() as clock:
        clock.setattr(worker.time, "time", lambda: started_at)
        worker._remember_command({"id": "t1"}, proc, str(log))
    return proc, log


def test_a_command_past_its_limit_is_killed_and_says_why(world, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(worker, "kill_process_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(worker, "_COMMANDS", {})
    _, log = _running_command(world, monkeypatch, started_at=1000.0)
    worker._stop_overdue_commands(1000.0 + time_limits.COMMAND_LIMIT_S - 1)
    assert killed == []
    worker._stop_overdue_commands(1000.0 + time_limits.COMMAND_LIMIT_S)
    assert killed == [4242]
    assert time_limits.COMMAND_STOPPED in log.read_text(encoding="utf-8")
    assert _runs() == [(task_runs.FAILED, time_limits.COMMAND_STOPPED)]
    worker._stop_overdue_commands(1000.0 + 2 * time_limits.COMMAND_LIMIT_S)
    assert killed == [4242]  # Forgotten once stopped.


def test_a_later_worker_still_stops_a_command(world, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(worker, "kill_process_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(worker, "_COMMANDS", {})
    _running_command(world, monkeypatch, started_at=1000.0)
    monkeypatch.setattr(worker, "_COMMANDS", {})  # A new Worker has only the file.
    worker._stop_overdue_commands(1000.0 + time_limits.COMMAND_LIMIT_S)
    assert killed == [4242]


def test_a_finished_command_is_forgotten_and_a_reused_pid_is_not_killed(world, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(worker, "kill_process_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(worker, "_COMMANDS", {})
    _running_command(world, monkeypatch, started_at=1000.0, proc=SimpleNamespace(pid=4242, poll=lambda: 0))
    worker._stop_overdue_commands(1001.0)
    assert worker._read_commands() == []
    _running_command(world, monkeypatch, started_at=1000.0)
    monkeypatch.setattr(worker, "_COMMANDS", {})
    monkeypatch.setattr(worker, "process_start_token", lambda _pid: "someone else")
    worker._stop_overdue_commands(1000.0 + time_limits.COMMAND_LIMIT_S)
    assert killed == []


def test_a_retry_of_an_old_run_has_its_own_clock(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    source_started = store.get_execution(active.execution_id).created_at
    driver._finish_attempt(active, _reply("done"), threading.Event())
    retried_at = source_started + 10 * time_limits.SCHEDULED_RUN_LIMIT_S
    retry = _admit(store, message="retry", parent=active.execution_id, speaker=SCHEDULER)
    with store._transaction() as connection:
        connection.update("executions", {"source_checkpoint_id": "saved_point", "created_at": retried_at},
                          execution_id=retry.execution_id)
    _limits_see(driver, store, monkeypatch)
    assert retry.execution_id not in time_limits.stop_overdue(retried_at + 30.0)
    assert store.get_execution(retry.execution_id).status is ExecutionStatus.QUEUED
    assert retry.execution_id in time_limits.stop_overdue(retried_at + time_limits.SCHEDULED_RUN_LIMIT_S)


def _stuck_past_its_limit(driver, active, store, monkeypatch):
    """Over a limit of nothing, so the fake clock only has to count the grace."""
    _limits_see(driver, store, monkeypatch)
    monkeypatch.setitem(time_limits._LIMITS, SCHEDULER, 0.0)
    time_limits.stop_overdue()
    stopping = store.get_execution(active.execution_id)
    assert stopping.status is ExecutionStatus.CANCELLING
    return store.list_commands(active.execution_id)[0].submitted_at


def test_a_turn_that_ignores_its_stop_is_ended_without_it(world, tmp_path, monkeypatch):
    from openprogram.runtime.conversation import run_control as conversation_control
    from openprogram.runtime.conversation.always_on import runtime as always_on

    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    event = threading.Event()
    assert conversation_control.claim_cancel_event(SESSION, event, execution_id=active.execution_id)
    stopped_at = _stuck_past_its_limit(driver, active, store, monkeypatch)
    time_limits.stop_overdue(stopped_at + time_limits.STOP_GRACE_S - 1)
    assert store.get_execution(active.execution_id).status is ExecutionStatus.CANCELLING
    assert conversation_control.is_turn_running(SESSION)

    time_limits.stop_overdue(stopped_at + time_limits.STOP_GRACE_S)
    ended = store.get_execution(active.execution_id)
    assert ended.status is ExecutionStatus.CANCELLED and ended.reason_code == time_limits.TIME_LIMIT
    assert always_on._stopped_at_time_limit(active.execution_id)  # What a stuck check-in is recorded by.
    assert not conversation_control.is_turn_running(SESSION)
    assert _runs() == [(task_runs.FAILED, time_limits.SCHEDULED_RUN_STOPPED)]

    event.set()
    driver._finish_attempt(active, _reply("woke up at last"), event)  # The thread returns much later.
    assert _runs() == [(task_runs.FAILED, time_limits.SCHEDULED_RUN_STOPPED)]


def test_a_process_that_ignores_its_stop_is_killed(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    stopped_at = _stuck_past_its_limit(driver, active, store, monkeypatch)
    execution = store.get_execution(active.execution_id)
    lease = {**execution.owner_lease, "process_owner": {"instance": "a-scheduled-run", "pid": 4242, "started": "then"}}
    with store._transaction() as connection:
        connection.update("executions", {"owner_lease_json": json.dumps(lease)}, execution_id=active.execution_id)
    from openprogram.runtime.execution import process_owner

    killed: list[int] = []
    monkeypatch.setattr(process_owner, "process_owner_may_be_alive", lambda _lease: True)
    monkeypatch.setattr(time_limits, "kill_process_tree", lambda pid: killed.append(pid) or True)
    time_limits.stop_overdue(stopped_at + time_limits.STOP_GRACE_S)
    assert killed == [4242]
    assert store.get_execution(active.execution_id).status is ExecutionStatus.CANCELLED
    assert _runs() == [(task_runs.FAILED, time_limits.SCHEDULED_RUN_STOPPED)]


def test_a_run_ended_without_its_stop_stops_the_command_it_was_waiting_on(world, tmp_path, monkeypatch):
    """Force-ended, a stuck run's waited-on command stops with it; a command it
    left in the background and another run's command go on."""
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    stopped_at = _stuck_past_its_limit(driver, active, store, monkeypatch)
    commands = _commands_of(store, active, tmp_path, monkeypatch)
    time_limits.stop_overdue(stopped_at + time_limits.STOP_GRACE_S - 1)
    assert commands.sent == []

    time_limits.stop_overdue(stopped_at + time_limits.STOP_GRACE_S)
    assert store.get_execution(active.execution_id).status is ExecutionStatus.CANCELLED
    _only_the_waited_on_command_stopped(commands)


def test_a_stop_someone_asked_for_is_never_forced(world, tmp_path, monkeypatch):
    driver, active, store = _ran_turn(tmp_path, speaker=SCHEDULER)
    _limits_see(driver, store, monkeypatch)
    _cancel(driver, active, store)
    monkeypatch.setitem(time_limits._LIMITS, SCHEDULER, 0.0)
    time_limits.stop_overdue(time.time() + 10 * time_limits.STOP_GRACE_S)
    assert store.get_execution(active.execution_id).status is ExecutionStatus.CANCELLING


def test_a_killed_command_leaves_no_zombie(world, monkeypatch):
    monkeypatch.setattr(worker, "_COMMANDS", {})
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        _running_command(world, monkeypatch, started_at=1000.0, proc=proc)
        worker._stop_overdue_commands(1000.0 + time_limits.COMMAND_LIMIT_S)
        deadline = time.time() + 5
        while worker._COMMANDS and time.time() < deadline:
            worker._stop_overdue_commands(1000.0 + time_limits.COMMAND_LIMIT_S)
            time.sleep(0.02)
        assert worker._COMMANDS == {} and proc.returncode == -9
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# --- catch-up of a recurring task's missed time -----------------------------

def _at(hour, minute=0, day=10):
    return datetime.datetime(2026, 11, day, hour, minute)


@pytest.fixture
def catch_up(tmp_path, monkeypatch):
    path = tmp_path / "tasks.json"
    monkeypatch.setenv("OPENPROGRAM_SCHEDULER_PATH", str(path))
    monkeypatch.setattr(run_finish, "finish_kept", lambda: None)
    monkeypatch.setattr(worker, "_stop_overdue_commands", lambda _now: None)
    fired: list[str] = []

    def spawn(entry, _log_dir, event=None):
        fired.append(entry["id"])
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(worker, "_spawn", spawn)
    return SimpleNamespace(path=path, fired=fired)


def _daily(path, *, stamped_at=_at(8, day=9), **changes):
    entry = {"id": "t1", "type": "recurring", "cron": "0 9 * * *", "enabled": True, "prompt": "look",
             "created_at": int(stamped_at.timestamp()), "updated_at": int(stamped_at.timestamp()), **changes}
    cron._save(str(path), [entry])


def _restart_tick(now):
    """A Worker just started: fresh state from disk, and its @reboot-only first tick."""
    worker._tick(worker._load_state(), reboot=True, now=now)
    return worker._load_state()


def test_a_restart_during_the_minute_fires_once(catch_up):
    _daily(catch_up.path)
    state = _restart_tick(_at(9))
    assert catch_up.fired == []
    worker._tick(state, now=_at(9, 1))
    worker._tick(state, now=_at(9, 2))
    assert catch_up.fired == ["t1"]


def test_a_restart_after_the_time_already_fired_adds_nothing(catch_up):
    _daily(catch_up.path)
    worker._tick({}, now=_at(9))
    state = _restart_tick(_at(9))
    worker._tick(state, now=_at(9, 1))
    assert catch_up.fired == ["t1"]


def test_many_missed_times_fire_one_run(catch_up):
    _daily(catch_up.path, cron="*/5 * * * *", stamped_at=_at(1))
    worker._tick({}, now=_at(9, 3))
    worker._tick(worker._load_state(), now=_at(9, 4))
    assert catch_up.fired == ["t1"]


@pytest.mark.parametrize("changes", [{"enabled": False}, {"updated_at": int(_at(9, 30).timestamp())}])
def test_a_disabled_or_edited_task_does_not_catch_up(catch_up, changes):
    _daily(catch_up.path, **changes)
    worker._tick({}, now=_at(10))
    assert catch_up.fired == [] and task_runs.recent("t1") == []


def test_an_entry_with_no_edit_time_waits_for_its_next_firing_then_catches_up(catch_up):
    _daily(catch_up.path)
    entries = cron._load(str(catch_up.path))
    del entries[0]["updated_at"]
    cron._save(str(catch_up.path), entries)
    worker._tick({}, now=_at(10))
    assert catch_up.fired == [] and task_runs.recent("t1") == []
    worker._tick({}, now=_at(9, day=11))
    assert catch_up.fired == ["t1"]
    assert cron._load(str(catch_up.path))[0]["updated_at"] == int(_at(9, day=11).timestamp())
    worker._tick(worker._load_state(), now=_at(10, day=12))
    assert catch_up.fired == ["t1", "t1"]


def test_a_time_over_twelve_hours_old_is_one_missed_row_across_restarts(catch_up):
    _daily(catch_up.path)
    worker._tick({}, now=_at(22))
    for hour in (22, 23):
        worker._tick(worker._load_state(), now=_at(hour, 30))
    assert catch_up.fired == []
    rows = task_runs.recent("t1")
    assert [row["status"] for row in rows] == [task_runs.MISSED]
    assert rows[0]["text"] == "2026-11-10 09:00" and rows[0]["count"] == 1
    assert rows[0]["at"] == _at(9).timestamp()  # the time it says OpenProgram was not running


def test_a_task_with_an_unreadable_time_does_not_stop_the_others(catch_up):
    stamped = int(_at(8, day=9).timestamp())
    task = {"type": "recurring", "cron": "0 9 * * *", "enabled": True, "prompt": "look",
            "created_at": stamped, "updated_at": stamped}
    cron._save(str(catch_up.path), [{**task, "id": "bad", "created_at": 10 ** 18}, {**task, "id": "good"}])
    state = {}
    worker._tick(state, now=_at(10))
    assert catch_up.fired == ["good"]
    worker._tick(state, now=_at(9, day=11))
    assert catch_up.fired == ["good", "bad", "good"]  # it only loses the catch-up


def test_missed_rows_keep_the_failure_count_and_collapse(catch_up):
    task_runs.record("t1", task_runs.FAILED, "broke")
    _daily(catch_up.path)
    worker._tick({}, now=_at(22))
    _daily(catch_up.path, stamped_at=_at(8, day=9))  # still before the next miss
    state = worker._load_state()
    worker._tick(state, now=_at(23, day=11))
    rows = task_runs.recent("t1")
    assert [row["status"] for row in rows] == [task_runs.MISSED, task_runs.FAILED]
    assert rows[0]["count"] == 2
    assert task_runs.record("t1", task_runs.FAILED, "again") == 2


def test_a_time_skipped_at_spring_forward_still_fires(catch_up):
    before = datetime.datetime(2026, 3, 1, 8)
    _daily(catch_up.path, cron="30 2 * * *", stamped_at=before)
    state = {"t1": {"last_fired_minute": "2026-03-07T02:30"}}
    worker._tick(state, now=datetime.datetime(2026, 3, 8, 3, 0))
    worker._tick(state, now=datetime.datetime(2026, 3, 8, 3, 1))
    assert catch_up.fired == ["t1"]


@pytest.mark.parametrize("changes", [{"cron": "@reboot"}, {"type": "monitor"}, {"type": "once"},
                                     {"execution": {"watch": "date"}}])
def test_only_a_plain_recurring_task_catches_up(catch_up, changes):
    _daily(catch_up.path, **changes)
    worker._tick({}, now=_at(10))
    assert catch_up.fired == [] and task_runs.recent("t1") == []


def test_a_catch_up_that_cannot_start_is_not_tried_again(catch_up, monkeypatch):
    _daily(catch_up.path)
    attempts = []
    monkeypatch.setattr(worker, "_spawn", lambda entry, _log_dir, event=None: attempts.append(entry["id"]))
    state = {}
    worker._tick(state, now=_at(10))
    worker._tick(state, now=_at(10, 1))
    assert attempts == ["t1"]


def test_a_scheduled_run_is_told_the_preferences_of_the_agent_running_it(world, monkeypatch):
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.conversation.always_on import store

    monkeypatch.setattr(manager, "get", lambda agent_id: SimpleNamespace(
        id=agent_id, always_on={"enabled": agent_id == "main"}))
    monkeypatch.setattr(store, "load_preferences", lambda agent_id: [{"id": "p", "text": f"Rule of {agent_id}"}])
    run = _child_run(monkeypatch, world, _handoff(), PAUSED)
    assert "- Rule of main" in run.request.run_note


def test_a_report_reaches_a_conversation_the_task_started(tmp_path, monkeypatch):
    """A task set outside any chat runs in its own conversation, where nobody spoke
    first: its run is the only branch and there is no head. The report starts the
    conversation's main line at ROOT instead of being refused for a missing predecessor."""
    from openprogram.runtime.conversation.always_on import runtime as always_on, store as reports

    root = tmp_path / "sessions"
    db, run_process = SessionDB(root), SessionDB(root)
    monkeypatch.setattr(always_on, "_db", lambda: db)
    monkeypatch.setattr(task_branch, "_db", lambda: run_process)
    run_process.create_session("cron-t1", "main")
    placement = task_branch.prepare("cron-t1", TASK, run_process)
    assert placement.parent is None
    # What the run's own process writes: ROOT, then the run beside the main line.
    run_process.append_message("cron-t1", {"id": "ROOT", "role": "user", "content": "", "display": "root"},
                               advance_head=False)
    run_process.append_message("cron-t1", {"id": placement.msg_id, "role": "user", "content": "check",
                                           "predecessor": "ROOT"}, advance_head=False)
    run_process.append_message("cron-t1", {"id": f"{placement.msg_id}_reply", "role": "assistant",
                                           "content": "In stock", "predecessor": placement.msg_id},
                               advance_head=False)
    assert (db.get_session("cron-t1") or {}).get("head_id") is None
    row = {"id": "r1", "task_id": "t1", "title": "VPS stock", "body": "In stock", "kind": "finding"}

    message_id = always_on.post_report_message("cron-t1", "main", row)

    assert message_id == reports.report_message_id(row)
    assert db.get_session("cron-t1")["head_id"] == message_id
    assert [message["id"] for message in db.get_branch("cron-t1")] == [message_id]
    # The task's branch is untouched and still where its next run goes.
    assert task_branch.bound_tip("cron-t1", "t1", run_process) is not None
