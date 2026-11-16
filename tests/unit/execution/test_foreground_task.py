"""Which execution counts as a conversation's foreground turn."""
from __future__ import annotations

import types

import pytest

from openprogram.runtime.execution.projections.foreground import (
    active_foreground_task,
    another_turn_runs,
    foreground_task_snapshot,
)
from openprogram.runtime.execution.model import ExecutionStatus


class _Store:
    def __init__(self, executions, source="web"):
        self._executions = executions
        self._source = source

    def list_nonterminal(self, *, session_id):
        return [e for e in self._executions if e.session_id == session_id]

    def get_agent_turn_input(self, _execution_id):
        return {"kind": "chat", "request": {"source": self._source, "interaction": None}}

    def get_execution_input(self, _execution_id):
        return types.SimpleNamespace(user_message_id="m1")


def _execution(status, reason_code=None):
    return types.SimpleNamespace(
        execution_id="e1", session_id="s1", status=status, status_version=4,
        reason_code=reason_code, current_attempt_id=None, created_at=1.0,
    )


@pytest.mark.parametrize("reason", ["wait_open", "system_access_required"])
def test_turn_paused_at_a_wait_stays_the_foreground_turn(reason):
    execution = _execution(ExecutionStatus.PAUSED, reason)
    store = _Store([execution])

    task = foreground_task_snapshot(store, execution)

    assert task is not None
    assert task["status"] == "paused"
    assert task["reason_code"] == reason
    assert task["execution_id"] == "e1"
    assert active_foreground_task(store, "s1") == task


def test_explicitly_paused_turn_releases_the_conversation():
    execution = _execution(ExecutionStatus.PAUSED, "pause.user")

    assert foreground_task_snapshot(_Store([execution]), execution) is None


def test_running_turn_is_foreground():
    execution = _execution(ExecutionStatus.RUNNING)

    assert foreground_task_snapshot(_Store([execution]), execution)["status"] == "running"


@pytest.mark.parametrize("source", ["telegram", "wechat"])
def test_chat_app_turn_paused_at_a_wait_keeps_the_conversation(source):
    execution = _execution(ExecutionStatus.PAUSED, "wait_open")

    assert foreground_task_snapshot(_Store([execution], source), execution)["msg_id"] == "m1"


def test_background_source_is_not_foreground():
    execution = _execution(ExecutionStatus.RUNNING)

    assert foreground_task_snapshot(_Store([execution], "scheduler"), execution) is None


def _turn(execution_id, status, *, parent=None):
    return types.SimpleNamespace(
        execution_id=execution_id, session_id="s1", status=status, status_version=1,
        reason_code=None, current_attempt_id=None, created_at=1.0, parent_execution_id=parent,
    )


class _Turns:
    """Executions with their own request and user message."""

    def __init__(self, turns):
        self._turns = turns

    def list_nonterminal(self, *, session_id):
        return [execution for execution, _request, _kind in self._turns.values()]

    def get_agent_turn_input(self, execution_id):
        _execution, request, kind = self._turns[execution_id]
        return {"kind": kind, "request": request}

    def get_execution_input(self, execution_id):
        return types.SimpleNamespace(user_message_id=execution_id + "_user")


def test_another_turn_runs_counts_only_other_live_chat_turns_of_the_conversation():
    """The turn that is ending, a paused turn, a Job, a sub-agent and a
    background check-in do not keep the conversation running; a Goal's
    queued next turn and a turn on another branch do."""
    own = {"notice": (_turn("notice", ExecutionStatus.RUNNING), {"source": "process_exit"}, "chat")}
    assert not another_turn_runs(_Turns(own), "s1", own_msg_id="notice_user")

    idle = {
        **own,
        "paused": (_turn("paused", ExecutionStatus.PAUSED), {"source": "web"}, "chat"),
        "job": (_turn("job", ExecutionStatus.RUNNING), {"source": "web"}, "job"),
        "child": (_turn("child", ExecutionStatus.RUNNING, parent="notice"), {"source": "web"}, "chat"),
        "check_in": (_turn("check_in", ExecutionStatus.RUNNING),
                     {"source": "always_on", "owns_conversation_effects": False}, "chat"),
    }
    assert not another_turn_runs(_Turns(idle), "s1", own_msg_id="notice_user")

    for status in (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING):
        busy = {**idle, "goal": (_turn("goal", status), {"source": "web", "branch_id": "b"}, "chat")}
        assert another_turn_runs(_Turns(busy), "s1", own_msg_id="notice_user")
