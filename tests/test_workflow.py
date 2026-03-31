"""
Tests for the Workflow class.
"""

import pytest
from pydantic import BaseModel
from harness.function import Function, FunctionError
from harness.session import Session
from harness.workflow import Workflow, FunctionCall


class MockSession(Session):
    def __init__(self, replies: list[str]):
        self._replies = iter(replies)

    def send(self, message: str) -> str:
        return next(self._replies)


class Result1(BaseModel):
    data: str


class Result2(BaseModel):
    processed: str


def test_workflow_runs_functions_in_order():
    """Workflow executes all functions and accumulates context."""
    session = MockSession([
        '{"data": "from fn1"}',
        '{"processed": "from fn2"}',
    ])

    fn1 = Function("fn1", "First function", "Do fn1", Result1)
    fn2 = Function("fn2", "Second function", "Do fn2", Result2)

    workflow = Workflow(
        calls=[FunctionCall(fn1), FunctionCall(fn2)],
        default_session=session,
    )
    result = workflow.run(task="test task")

    assert result.success is True
    assert result.context["fn1"]["data"] == "from fn1"
    assert result.context["fn2"]["processed"] == "from fn2"


def test_workflow_stops_on_function_error():
    """Workflow stops and reports failure when a function raises FunctionError."""
    session = MockSession([
        "not valid json",
        "not valid json",
        "not valid json",
    ])

    fn1 = Function("fn1", "First function", "Do fn1", Result1, max_retries=3)
    fn2 = Function("fn2", "Second function", "Do fn2", Result2)

    workflow = Workflow(
        calls=[FunctionCall(fn1), FunctionCall(fn2)],
        default_session=session,
    )
    result = workflow.run(task="test task")

    assert result.success is False
    assert result.failed_function == "fn1"
    assert "fn2" not in result.context


def test_workflow_passes_return_values_as_context():
    """Each function can read return values of previous functions via context."""
    received_messages = []

    class CapturingSession(Session):
        _replies = iter([
            '{"data": "fn1 output"}',
            '{"processed": "used fn1 data"}',
        ])

        def send(self, message: str) -> str:
            received_messages.append(message)
            return next(self._replies)

    fn1 = Function("fn1", "First", "Do 1", Result1)
    fn2 = Function("fn2", "Second", "Do 2", Result2, params=["task", "fn1"])

    workflow = Workflow(
        calls=[FunctionCall(fn1), FunctionCall(fn2)],
        default_session=CapturingSession(),
    )
    workflow.run(task="my task")

    # fn2's call message should contain fn1's return value
    assert "fn1 output" in received_messages[1]
