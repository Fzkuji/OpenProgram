"""
Tests for the Programmer class.
"""

import json
import pytest
from pydantic import BaseModel
from harness.function import Function
from harness.session import Session
from harness.runtime import Runtime
from harness.programmer import Programmer, ProgrammerDecision


# --- Mock sessions ---

class SequentialSession(Session):
    """Returns replies in order."""
    def __init__(self, replies: list[str]):
        self._replies = iter(replies)

    def send(self, message) -> str:
        return next(self._replies)


class SimpleResult(BaseModel):
    data: str


# --- Tests ---

def test_programmer_calls_function_and_finishes():
    """Programmer calls a function, gets result, then says done."""
    programmer_replies = [
        json.dumps({
            "action": "call",
            "reasoning": "Need to run fn1",
            "function_name": "fn1",
            "function_args": {"task": "test"},
        }),
        json.dumps({
            "action": "done",
            "reasoning": "fn1 succeeded, task complete",
        }),
    ]

    runtime_reply = '{"data": "fn1 result"}'

    programmer = Programmer(
        session=SequentialSession(programmer_replies),
        runtime=Runtime(session_factory=lambda: SequentialSession([runtime_reply])),
        functions=[Function("fn1", "Test fn", "Do fn1", SimpleResult)],
    )

    result = programmer.run("test task")
    assert result.success is True
    assert result.iterations == 2
    assert len(result.log) == 1  # one function call logged
    assert "fn1()" in result.log[0]


def test_programmer_fails_gracefully():
    """Programmer can decide the task is impossible."""
    programmer_replies = [
        json.dumps({
            "action": "fail",
            "reasoning": "Cannot find the application",
            "failure_reason": "App not installed",
        }),
    ]

    programmer = Programmer(
        session=SequentialSession(programmer_replies),
        runtime=Runtime(session_factory=lambda: SequentialSession([])),
        functions=[],
    )

    result = programmer.run("open nonexistent app")
    assert result.success is False
    assert result.failure_reason == "App not installed"
    assert result.iterations == 1


def test_programmer_creates_new_function():
    """Programmer creates a new function and then calls it."""
    programmer_replies = [
        json.dumps({
            "action": "create",
            "reasoning": "Need a custom function",
            "new_function": {
                "name": "custom_fn",
                "docstring": "A custom function",
                "body": "Do custom thing",
                "params": ["task"],
                "return_type_schema": {
                    "type": "object",
                    "properties": {
                        "data": {"type": "string"}
                    },
                    "required": ["data"]
                }
            }
        }),
        json.dumps({
            "action": "call",
            "reasoning": "Now call the new function",
            "function_name": "custom_fn",
            "function_args": {"task": "test"},
        }),
        json.dumps({
            "action": "done",
            "reasoning": "Task complete",
        }),
    ]

    runtime_reply = '{"data": "custom result"}'

    programmer = Programmer(
        session=SequentialSession(programmer_replies),
        runtime=Runtime(session_factory=lambda: SequentialSession([runtime_reply])),
        functions=[],
    )

    result = programmer.run("do custom thing")
    assert result.success is True
    assert "custom_fn" in programmer.functions
    assert result.iterations == 3
    assert len(result.log) == 2  # create + call


def test_programmer_handles_missing_function():
    """Programmer gracefully handles calling a non-existent function."""
    programmer_replies = [
        json.dumps({
            "action": "call",
            "reasoning": "Call nonexistent",
            "function_name": "does_not_exist",
        }),
        json.dumps({
            "action": "fail",
            "reasoning": "Function not found",
            "failure_reason": "does_not_exist is not available",
        }),
    ]

    programmer = Programmer(
        session=SequentialSession(programmer_replies),
        runtime=Runtime(session_factory=lambda: SequentialSession([])),
        functions=[],
    )

    result = programmer.run("test")
    assert result.success is False
    assert result.iterations == 2
    # Log should show the failed call
    assert any("error" in entry.lower() or "✗" in entry for entry in result.log)


def test_programmer_max_iterations():
    """Programmer stops after max_iterations."""
    def make_call_reply():
        return json.dumps({
            "action": "call",
            "reasoning": "Keep going",
            "function_name": "fn1",
        })

    programmer_replies = [make_call_reply() for _ in range(10)]
    runtime_reply = '{"data": "ok"}'

    programmer = Programmer(
        session=SequentialSession(programmer_replies),
        runtime=Runtime(session_factory=lambda: SequentialSession([runtime_reply])),
        functions=[Function("fn1", "Test", "Do it", SimpleResult)],
        max_iterations=5,
    )

    result = programmer.run("infinite task")
    assert result.success is False
    assert "Max iterations" in result.failure_reason
    assert result.iterations == 5
    assert len(result.log) == 5  # 5 function calls logged
