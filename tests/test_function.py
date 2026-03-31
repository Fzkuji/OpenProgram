"""
Tests for the Function class.

Run with:
    pip install pytest
    pytest tests/
"""

import pytest
from pydantic import BaseModel
from harness.function import Function, FunctionError
from harness.session import Session


# --- Mock session for testing ---

class MockSession(Session):
    """A session that returns predefined replies in order."""

    def __init__(self, replies: list[str]):
        self._replies = iter(replies)

    def send(self, message: str) -> str:
        return next(self._replies)


# --- Return types ---

class SimpleResult(BaseModel):
    status: str
    value: str


# --- Tests ---

def test_function_returns_valid_output():
    """Function returns typed result when session gives valid JSON."""
    session = MockSession([
        '{"status": "ok", "value": "hello"}'
    ])
    fn = Function(
        name="test",
        docstring="A test function",
        body="Do the thing",
        return_type=SimpleResult,
    )
    result = fn.call(session=session, context={"task": "test task"})
    assert isinstance(result, SimpleResult)
    assert result.status == "ok"
    assert result.value == "hello"


def test_function_retries_on_invalid_return_value():
    """Function retries when session gives invalid JSON, succeeds on second try."""
    session = MockSession([
        "I don't know what to do",                   # invalid — retry
        '{"status": "ok", "value": "retry worked"}'  # valid
    ])
    fn = Function(
        name="test",
        docstring="A test function",
        body="Do the thing",
        return_type=SimpleResult,
        max_retries=2,
    )
    result = fn.call(session=session, context={"task": "test task"})
    assert result.status == "ok"


def test_function_raises_after_max_retries():
    """Function raises FunctionError after exhausting retries."""
    session = MockSession([
        "not json",
        "still not json",
        "nope",
    ])
    fn = Function(
        name="test",
        docstring="A test function",
        body="Do the thing",
        return_type=SimpleResult,
        max_retries=3,
    )
    with pytest.raises(FunctionError) as exc_info:
        fn.call(session=session, context={"task": "test task"})
    assert exc_info.value.function_name == "test"
    assert exc_info.value.attempts == 3


def test_function_extracts_only_declared_params():
    """Function only passes declared params from context as arguments."""
    received_messages = []

    class CapturingSession(Session):
        def send(self, message: str) -> str:
            received_messages.append(message)
            return '{"status": "ok", "value": "done"}'

    fn = Function(
        name="test",
        docstring="A test function",
        body="Do the thing",
        return_type=SimpleResult,
        params=["task", "previous_result"],
    )
    context = {
        "task": "my task",
        "previous_result": "something",
        "secret_data": "should not appear",
    }
    fn.call(session=CapturingSession(), context=context)

    message = received_messages[0]
    assert "my task" in message
    assert "something" in message
    assert "secret_data" not in message


def test_function_parses_json_in_markdown_block():
    """Function handles return values wrapped in markdown code blocks."""
    session = MockSession([
        '```json\n{"status": "ok", "value": "from markdown"}\n```'
    ])
    fn = Function(
        name="test",
        docstring="A test function",
        body="Do the thing",
        return_type=SimpleResult,
    )
    result = fn.call(session=session, context={"task": "test task"})
    assert result.value == "from markdown"


def test_function_includes_examples_in_call_message():
    """Function includes examples in the assembled call message."""
    received_messages = []

    class CapturingSession(Session):
        def send(self, message: str) -> str:
            received_messages.append(message)
            return '{"status": "ok", "value": "done"}'

    fn = Function(
        name="test",
        docstring="A test function",
        body="Do the thing",
        return_type=SimpleResult,
        examples=[
            {"input": {"task": "example"}, "output": {"status": "ok", "value": "example"}}
        ]
    )
    fn.call(session=CapturingSession(), context={"task": "real task"})
    assert "Examples" in received_messages[0]
