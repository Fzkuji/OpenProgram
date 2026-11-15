"""Which execution counts as a conversation's foreground turn."""
from __future__ import annotations

import types

import pytest

from openprogram.runtime.execution.foreground import (
    active_foreground_task,
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
