"""Everything that touches one conversation's Goal, at once.

Real admission, driver, control service, usage ledger and provider stream;
only the model is a stand-in. While a Goal works through its turns, other
threads refresh its usage, read it, change its budget, hold its lock and run
turns on another branch. No reply may fail and the Goal's books must add up.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

from openprogram.models.providers.types import Usage
from tests.component.agent import test_chat_goal as _test_chat_goal
from tests.component.agent.test_goal_request_usage import final, run

# Shared pytest fixture, bound at module level so pytest collects it here.
runtime = _test_chat_goal.runtime

SID = "goal-chat"
TOKENS = 120  # one stand-in provider request: 100 in, 20 out
AUTHORITY = {"speaker_kind": "owner", "speaker_id": "owner/local",
             "principal_id": "owner/install/0123456789abcdef", "authority_tier": "owner",
             "interaction": "interactive"}


class Provider:
    requires_credentials = False

    async def stream_simple(self, m, context, options):
        yield final(m, Usage(input=100, output=20))


@pytest.fixture
def books(runtime, tmp_path, monkeypatch):
    """The Goal runtime with a real usage ledger and a second recorded branch."""
    from openprogram.models.usage import context as usage_context, ledger, recorder
    from openprogram.models.usage.context import UsageContext
    from openprogram.models.usage.ledger import UsageLedger
    goals, chat, store = runtime
    meter = UsageLedger(tmp_path / "usage.db")
    monkeypatch.setattr(ledger, "default_ledger", meter)
    monkeypatch.setattr(recorder, "default_ledger", meter)
    # The stand-in runner writes no turn journal for the tool-call scan to read.
    monkeypatch.setattr(chat, "used_tools", lambda *_: True)
    token = usage_context._current.set(UsageContext())
    branches = SimpleNamespace(
        goal=goals.load_goal(SID)["branch_id"],
        other=goals._db().reserve_task_branch(SID, task_id="aside", msg_id="aside-1", agent_id="main"))
    assert branches.goal and branches.other and branches.goal != branches.other
    try:
        yield goals, chat, store, branches
    finally:
        usage_context._current.reset(token)
        deadline = time.monotonic() + 30
        while chat._settling and time.monotonic() < deadline:
            time.sleep(0.01)
        meter.close()


def _adapter(monkeypatch, runner):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter",
                        lambda **kw: CanonicalAgentAdapter(turn_runner=runner, **kw))
    return CanonicalAgentAdapter(turn_runner=runner)


def _request(text, **fields):
    from openprogram.runtime.agent.turn_request import TurnRequest
    return TurnRequest(SID, text, "main", "web", permission_mode="ask", **AUTHORITY, **fields)


def _start(adapter, request, message_id):
    admission = adapter.admit(request, trusted_actor=AUTHORITY, user_message_id=message_id,
                              assistant_message_id=message_id + "_reply",
                              config_snapshot_ref="session:" + SID)
    asyncio.run(adapter.activate(admission))
    return admission


class Hammer:
    """Threads that each repeat one thing until told to stop."""

    def __init__(self, goals):
        self.goals = goals
        self.stop = threading.Event()
        self.errors: list[BaseException] = []
        self.slowest = 0.0
        self.threads: list[threading.Thread] = []

    def repeat(self, action, *, until=None):
        def body():
            while not self.stop.is_set() and not (until is not None and until.is_set()):
                started = time.monotonic()
                try:
                    action()
                except BaseException as exc:
                    self.errors.append(exc)
                self.slowest = max(self.slowest, time.monotonic() - started)
                time.sleep(0.002)
        thread = threading.Thread(target=body, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread

    def end(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(30)
        assert not any(thread.is_alive() for thread in self.threads)


def _wait(condition, seconds=60.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_a_goal_keeps_its_books_while_everything_touches_it(books, monkeypatch, tmp_path):
    from openprogram.models.providers.types import AgentTool, Model
    from openprogram.models.usage.context import usage_scope
    from openprogram.programs.tools.files.read import read
    from openprogram.programs.tools.planning.todo import shared
    from openprogram.runtime.conversation import turn_runtime as dispatcher
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.runtime.execution.model import ExecutionStatus
    from openprogram.runtime.execution.run_control import get_current_execution_id
    goals, chat, store, branches = books
    work_turns, requests_per_turn = 4, 3
    artifact = tmp_path / "result.txt"
    artifact.write_text("Task result: verified output")
    fake = AgentTool(name="bash", label="forged", description="not a builtin", parameters={}, execute=lambda *a: None)
    monkeypatch.setattr(loop_runner, "_resolve_tools", lambda *a, **kw: [read, fake])
    monkeypatch.setattr(dispatcher, "_load_agent_profile", lambda *a: {})
    monkeypatch.setattr(dispatcher, "_resolve_model", lambda *a: Model(
        id="fake", name="fake", provider="openai", api="openai-completions", base_url="https://example.invalid"))
    monkeypatch.setattr("openprogram.runtime.context.components.build_system_prompt", lambda *a, **kw: "System")
    monkeypatch.setattr(shared, "todos_path", lambda sid: tmp_path / "todos.json")

    hammer = Hammer(goals)
    guard = threading.Lock()
    seen = {"goal_turns": [], "running": 0, "overlap": 0, "requests": 0, "worked": 0.0, "side_turns": 0}
    turn_errors: list[BaseException] = []
    quiet = threading.Event()  # nothing else may change the Goal's controls or run beside its verifier

    def persist(request, text):
        source = store.get_execution_input(get_current_execution_id())
        db = goals._db()
        branch = db.get_branch(request.session_id)
        db.append_message(request.session_id, {
            "id": source.assistant_message_id, "role": "assistant", "content": text,
            "predecessor": branch[-1]["id"] if branch else None})

    def goal_turn(request):
        if request.goal_verification:
            _, tools, *_ = loop_runner.resolve_agent_runtime(request)
            result = asyncio.run(tools[0].execute("inspect-result", {"file_path": str(artifact)}, None, None))
            assert not result.is_error
            goal = goals.load_goal(SID)
            refs = [key for key in goal["verification"]["evidence"] if key.startswith("read:")]
            assert refs
            persist(request, json.dumps({"reason": "Independent assessment", "requirements": [
                {"id": item["id"], "verdict": "met", "reason": "Inspected the result", "evidence": refs}
                for item in goal["verification"]["requirements"]]}))
            return
        for _ in range(requests_per_turn):
            run(Provider())
            with guard:
                seen["requests"] += 1
            time.sleep(0.02)
        if len(seen["goal_turns"]) == work_turns:
            quiet.set()
            for thread in quieted:
                thread.join(30)
            assert _wait(lambda: not side_running.is_set(), 30)
            persist(request, "I claim the task is complete.")
            chat.update(SID, "complete", expected=chat.current_identity())

    side_running = threading.Event()

    def runner(*, request, cancel_event):
        on_goal = request.branch_id == branches.goal
        started = time.monotonic()
        try:
            with usage_scope(session_id=request.session_id, call_kind="chat"):
                if not on_goal:
                    for _ in range(2):  # another branch's turn: its usage events refresh the Goal too
                        run(Provider())
                    with guard:
                        seen["side_turns"] += 1
                    return SimpleNamespace(failed=False)
                with guard:
                    seen["goal_turns"].append(get_current_execution_id())
                    seen["running"] += 1
                    seen["overlap"] = max(seen["overlap"], seen["running"])
                try:
                    goal_turn(request)
                finally:
                    with guard:
                        seen["running"] -= 1
                        seen["worked"] += time.monotonic() - started
        except BaseException as exc:
            turn_errors.append(exc)
            return SimpleNamespace(failed=True)
        return SimpleNamespace(failed=False)

    adapter = _adapter(monkeypatch, runner)
    side_ids: list[str] = []

    def side_turn():
        side_running.set()
        try:
            index = len(side_ids)
            side_ids.append(_start(adapter, _request("aside", branch_id=branches.other, side_branch=True),
                                   f"side{index}").execution_id)
        finally:
            side_running.clear()

    def slow_writer():
        with chat.locked(SID):
            time.sleep(0.03)

    def read_goal():  # GET /api/sessions/{id}/goal
        goal = goals.load_goal(SID)
        chat.recover_unsettled(SID, goal)
        goals.goal_projection(goal, SID)

    limits = iter(range(1, 10**9))

    def change_budget():
        goals.apply_goal_action(SID, "budget", max_elapsed_s=10**6 + next(limits))

    quieted = [hammer.repeat(change_budget, until=quiet), hammer.repeat(side_turn, until=quiet)]
    for action in (lambda: chat.refresh_usage(SID), lambda: chat.refresh_todos(SID), read_goal, slow_writer):
        hammer.repeat(action)
    began = time.monotonic()
    try:
        first = _start(adapter, _request("work", branch_id=branches.goal, tools_override=["read", "update_goal"]), "u1")
        assert _wait(lambda: goals.load_goal(SID)["status"] != "active" and not chat._settling), goals.load_goal(SID)
    finally:
        hammer.end()
    wall = time.monotonic() - began

    assert not turn_errors, turn_errors
    assert not hammer.errors, hammer.errors
    assert hammer.slowest < 10, hammer.slowest  # nobody sat out a lock wait
    goal = goals.load_goal(SID)
    assert goal["status"] == "achieved", (goal["status"], goal.get("pause_reason"), goal.get("last_reason"))
    turns = seen["goal_turns"]
    assert turns[0] == first.execution_id and len(turns) == len(set(turns)) == work_turns + 1
    assert seen["overlap"] == 1  # one turn of the Goal at a time
    assert seen["side_turns"] > 0 and seen["side_turns"] == len(side_ids)
    for execution_id in (*turns, *side_ids):
        assert store.get_execution(execution_id).status is ExecutionStatus.COMPLETED
    # Every turn counted once, every request's tokens once, no time lost or invented.
    assert goal["run_turns"] == len(turns)
    assert goal["accounted_execution_id"] == goal["execution_id"] == turns[-1]
    assert seen["requests"] == work_turns * requests_per_turn
    assert goal["usage"]["total_tokens"] == seen["requests"] * TOKENS
    assert goal["active_started_at"] is None
    assert goal["usage"].get("active_time_known") is not False
    assert seen["worked"] * 0.95 <= goal["usage"]["active_elapsed_s"] <= wall
    assert not chat._queues and not chat._refresh_waiting


@pytest.mark.parametrize("command", ["pause", "clear"])
def test_a_goal_command_typed_during_a_turn_lands(books, monkeypatch, command):
    from openprogram.models.usage.context import usage_scope
    from openprogram.runtime.execution.model import TERMINAL_EXECUTION_STATUSES
    goals, chat, store, branches = books
    hammer = Hammer(goals)
    requests, turns, turn_errors = [], [], []

    def runner(*, request, cancel_event):
        turns.append(request)
        try:
            with usage_scope(session_id=request.session_id, call_kind="chat"):
                deadline = time.monotonic() + 30
                while not cancel_event.is_set() and time.monotonic() < deadline:
                    try:
                        run(Provider())
                    except goals.GoalConflictError:
                        break  # the Goal no longer permits provider requests
                    requests.append(1)
                    time.sleep(0.01)
        except BaseException as exc:
            turn_errors.append(exc)
        return SimpleNamespace(failed=False)

    adapter = _adapter(monkeypatch, runner)

    def slow_writer():
        with chat.locked(SID):
            time.sleep(0.03)

    for action in (lambda: chat.refresh_usage(SID), slow_writer,
                   lambda: chat.recover_unsettled(SID, goals.load_goal(SID))):
        hammer.repeat(action)
    admissions = []
    turn = threading.Thread(target=lambda: admissions.append(
        _start(adapter, _request("work", branch_id=branches.goal), "u1")), daemon=True)
    turn.start()
    try:
        assert _wait(lambda: len(requests) >= 2)
        started = time.monotonic()
        reply = goals.handle_goal_command(SID, command)
        assert time.monotonic() - started < 10, reply
        turn.join(30)
        assert admissions and _wait(
            lambda: store.get_execution(admissions[0].execution_id).status in TERMINAL_EXECUTION_STATUSES)
        assert _wait(lambda: goals.load_goal(SID).get("accounted_execution_id") == admissions[0].execution_id
                     and not chat._settling), goals.load_goal(SID)
    finally:
        hammer.end()
    assert not turn_errors and not hammer.errors, (turn_errors, hammer.errors)
    assert hammer.slowest < 10
    goal = goals.load_goal(SID)
    assert goal["status"] == ("paused" if command == "pause" else "cancelled"), reply
    assert len(turns) == 1 and goal["run_turns"] == 1  # the turn is counted once and nothing follows it
    assert goal["active_started_at"] is None
    assert goal["usage"]["total_tokens"] == len(requests) * TOKENS
    if command == "pause":
        assert "Resuming" in goals.handle_goal_command(SID, "resume")["text"]
        resumed = goals.load_goal(SID)
        assert resumed["status"] == "active" and resumed["usage"]["total_tokens"] == len(requests) * TOKENS


def test_a_goal_continues_on_its_branch_while_another_branch_runs(books, monkeypatch):
    """The person selected another branch and a long turn runs there."""
    goals, chat, store, branches = books
    release, other_started = threading.Event(), threading.Event()
    goal_turns = []

    def runner(*, request, cancel_event):
        if request.branch_id != branches.goal:
            other_started.set()
            release.wait(30)
            return SimpleNamespace(failed=False)
        goal_turns.append(request)
        if len(goal_turns) == 2:
            goals.apply_goal_action(SID, "pause")
        return SimpleNamespace(failed=False)

    adapter = _adapter(monkeypatch, runner)
    other = threading.Thread(target=lambda: _start(adapter, _request("elsewhere", branch_id=branches.other), "o1"),
                             daemon=True)
    other.start()
    try:
        assert other_started.wait(30)
        _start(adapter, _request("work", branch_id=branches.goal), "u1")
        assert _wait(lambda: len(goal_turns) == 2, 30), goals.load_goal(SID)
        assert not release.is_set()
    finally:
        release.set()
        other.join(30)
    assert _wait(lambda: goals.load_goal(SID)["status"] == "paused" and not chat._settling)


def test_a_turn_step_without_the_lock_redoes_a_write_that_met_a_change(runtime, monkeypatch):
    from tests.component.agent.test_goal_lock_contention import _hold
    goals, chat, store = runtime
    goal = goals.load_goal(SID)
    goal["execution_id"] = "exec-running"
    goals.save_goal(SID, goal)
    monkeypatch.setattr(store, "get_execution", lambda _id: SimpleNamespace(
        execution_id="exec-running", session_id=SID, current_attempt_id="attempt-1"))
    monkeypatch.setattr(chat, "GOAL_LOCK_WAIT_S", 0.05)
    real_save, raced = goals.save_goal, []

    def save(session_id, value):
        if not raced:  # the lock's holder commits between this step's read and its write
            raced.append(1)
            other = goals.load_goal(session_id)
            other["budget"] = {"max_elapsed_s": 99.0}
            real_save(session_id, other)
        return real_save(session_id, value)

    monkeypatch.setattr(goals, "save_goal", save)
    thread, release = _hold(goals, 30)
    try:
        with chat.turn_context(SID, "exec-running", chat.identity(goal)):
            working = goals.load_goal(SID)
            assert working["phase"] == "working" and working["active_started_at"]
            assert working["budget"]["max_elapsed_s"] == 99.0  # the other writer's change is kept
    finally:
        release.set()
        thread.join(30)
    assert raced and goals.load_goal(SID)["active_started_at"] is None


def test_a_usage_refresh_never_holds_a_stream_behind_a_held_lock(runtime, monkeypatch):
    from tests.component.agent.test_goal_lock_contention import _hold
    goals, chat, _store = runtime
    monkeypatch.setattr(chat, "REFRESH_LOCK_WAIT_S", 0.3)
    thread, release = _hold(goals, 30)
    outcomes = []

    def first():
        try:
            chat.refresh_usage(SID)
        except goals.GoalBusy:
            outcomes.append("busy")

    waiter = threading.Thread(target=first)
    try:
        started = time.monotonic()
        waiter.start()
        assert _wait(lambda: SID in chat._refresh_waiting, 10)
        chat.refresh_usage(SID)  # one is already in line; it reads the ledger after this
        assert time.monotonic() - started < 0.25
        waiter.join(30)
        assert outcomes == ["busy"] and time.monotonic() - started < 10
    finally:
        release.set()
        thread.join(30)
    assert not chat._refresh_waiting
    chat.refresh_usage(SID)
