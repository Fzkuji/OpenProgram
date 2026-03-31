"""
Tests for the Step class.

Run with:
    pip install pytest
    pytest tests/
"""

import pytest
from pydantic import BaseModel
from harness.step import Step, StepFailure
from harness.session import Session


# --- Mock session for testing ---

class MockSession(Session):
    """A session that returns predefined replies."""

    def __init__(self, replies: list[str]):
        self._replies = iter(replies)

    def send(self, message: str) -> str:
        return next(self._replies)


# --- Output schemas ---

class SimpleResult(BaseModel):
    status: str
    value: str


# --- Tests ---

def test_step_returns_valid_output():
    """Step returns structured result when session gives valid JSON."""
    session = MockSession([
        '{"status": "ok", "value": "hello"}'
    ])
    step = Step(
        name="test",
        description="A test step",
        instructions="Do the thing",
        output_schema=SimpleResult,
    )
    result = step.run(session=session, context={"task": "test task"})
    assert isinstance(result, SimpleResult)
    assert result.status == "ok"
    assert result.value == "hello"


def test_step_retries_on_invalid_output():
    """Step retries when session gives invalid JSON, succeeds on second try."""
    session = MockSession([
        "I don't know what to do",               # invalid — retry
        '{"status": "ok", "value": "retry worked"}'  # valid
    ])
    step = Step(
        name="test",
        description="A test step",
        instructions="Do the thing",
        output_schema=SimpleResult,
        max_retries=2,
    )
    result = step.run(session=session, context={"task": "test task"})
    assert result.status == "ok"


def test_step_raises_after_max_retries():
    """Step raises StepFailure after exhausting retries."""
    session = MockSession([
        "not json",
        "still not json",
        "nope",
    ])
    step = Step(
        name="test",
        description="A test step",
        instructions="Do the thing",
        output_schema=SimpleResult,
        max_retries=3,
    )
    with pytest.raises(StepFailure) as exc_info:
        step.run(session=session, context={"task": "test task"})
    assert exc_info.value.step_name == "test"
    assert exc_info.value.attempts == 3


def test_step_extracts_only_declared_reads():
    """Step only passes declared fields from context to session."""
    received_messages = []

    class CapturingSession(Session):
        def send(self, message: str) -> str:
            received_messages.append(message)
            return '{"status": "ok", "value": "done"}'

    step = Step(
        name="test",
        description="A test step",
        instructions="Do the thing",
        output_schema=SimpleResult,
        reads=["task", "previous_result"],  # only these two
    )
    context = {
        "task": "my task",
        "previous_result": "something",
        "secret_data": "should not appear",
    }
    step.run(session=CapturingSession(), context=context)

    message = received_messages[0]
    assert "my task" in message
    assert "something" in message
    assert "secret_data" not in message


def test_step_parses_json_in_markdown_block():
    """Step handles JSON wrapped in markdown code blocks."""
    session = MockSession([
        '```json\n{"status": "ok", "value": "from markdown"}\n```'
    ])
    step = Step(
        name="test",
        description="A test step",
        instructions="Do the thing",
        output_schema=SimpleResult,
    )
    result = step.run(session=session, context={"task": "test task"})
    assert result.value == "from markdown"


def test_step_assembles_message_with_examples():
    """Step includes examples in the assembled message."""
    received_messages = []

    class CapturingSession(Session):
        def send(self, message: str) -> str:
            received_messages.append(message)
            return '{"status": "ok", "value": "done"}'

    step = Step(
        name="test",
        description="A test step",
        instructions="Do the thing",
        output_schema=SimpleResult,
        examples=[
            {"input": {"task": "example task"}, "output": {"status": "ok", "value": "example"}}
        ]
    )
    step.run(session=CapturingSession(), context={"task": "real task"})
    assert "Examples" in received_messages[0]
