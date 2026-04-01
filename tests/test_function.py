"""
Tests for @function decorator and built-in functions.
"""

import json
import pytest
from pydantic import BaseModel

from harness import function, FunctionError, ask, extract, summarize, classify, decide
from harness.session import Session, Message


# --- Mock Session ---

class MockSession(Session):
    """Returns pre-configured replies."""

    def __init__(self, replies: list[str] = None):
        self._replies = list(replies) if replies else []
        self._messages = []
        self._call_count = 0

    def send(self, message: Message) -> str:
        self._messages.append(message if isinstance(message, str) else str(message))
        self._call_count += 1
        if self._replies:
            return self._replies.pop(0)
        return '{"status": "ok"}'


# --- Return types ---

class ObserveResult(BaseModel):
    elements: list[str]
    target_visible: bool


class SimpleResult(BaseModel):
    status: str


# --- @function decorator ---

def test_function_basic():
    """Decorated function calls LLM and returns typed result."""
    session = MockSession(replies=['{"elements": ["button", "input"], "target_visible": true}'])

    @function(return_type=ObserveResult)
    def observe(session: Session, task: str) -> ObserveResult:
        """Observe the screen and find UI elements."""

    result = observe(session, task="find login")
    assert isinstance(result, ObserveResult)
    assert result.target_visible is True
    assert "button" in result.elements


def test_function_retries_on_invalid_json():
    """Function retries when LLM returns invalid JSON."""
    session = MockSession(replies=[
        "not json",
        '{"status": "ok"}',
    ])

    @function(return_type=SimpleResult)
    def simple(session: Session) -> SimpleResult:
        """Do something simple."""

    result = simple(session)
    assert result.status == "ok"
    assert session._call_count == 2  # retried once


def test_function_fails_after_max_retries():
    """Function raises FunctionError after max retries."""
    session = MockSession(replies=["bad", "bad", "bad"])

    @function(return_type=SimpleResult, max_retries=3)
    def failing(session: Session) -> SimpleResult:
        """Always fails."""

    with pytest.raises(FunctionError, match="failing"):
        failing(session)


def test_function_strips_markdown():
    """Function handles markdown-wrapped JSON."""
    session = MockSession(replies=['```json\n{"status": "ok"}\n```'])

    @function(return_type=SimpleResult)
    def simple(session: Session) -> SimpleResult:
        """Do it."""

    result = simple(session)
    assert result.status == "ok"


def test_function_with_examples():
    """Function includes examples in prompt."""
    session = MockSession(replies=['{"status": "ok"}'])

    @function(
        return_type=SimpleResult,
        examples=[{"input": {"task": "test"}, "output": {"status": "done"}}]
    )
    def with_examples(session: Session, task: str) -> SimpleResult:
        """Do something."""

    result = with_examples(session, task="test")
    assert result.status == "ok"
    # Check examples were in the prompt
    assert "Examples" in session._messages[0]


def test_function_metadata():
    """Decorated function has metadata attached."""
    @function(return_type=SimpleResult, max_retries=5)
    def my_func(session: Session) -> SimpleResult:
        """My docstring."""

    assert my_func._is_function is True
    assert my_func._return_type == SimpleResult
    assert my_func._max_retries == 5
    assert my_func._fn_name == "my_func"
    assert "My docstring" in my_func._fn_doc


def test_function_preserves_name():
    """Decorated function preserves original name."""
    @function(return_type=SimpleResult)
    def my_function(session: Session) -> SimpleResult:
        """Doc."""

    assert my_function.__name__ == "my_function"
    assert "Doc" in my_function.__doc__


# --- Built-in functions ---

def test_ask():
    """ask() returns plain text."""
    session = MockSession(replies=["The answer is 42"])
    result = ask(session, "What is the answer?")
    assert result == "The answer is 42"


def test_extract():
    """extract() returns a Pydantic model."""
    session = MockSession(replies=['{"status": "extracted"}'])
    result = extract(session, "some text", SimpleResult)
    assert result.status == "extracted"


def test_summarize():
    """summarize() returns text."""
    session = MockSession(replies=["Short summary"])
    result = summarize(session, "Very long text " * 100)
    assert result == "Short summary"


def test_classify():
    """classify() returns a category."""
    session = MockSession(replies=["positive"])
    result = classify(session, "I love this!", ["positive", "negative", "neutral"])
    assert result == "positive"


def test_classify_case_insensitive():
    """classify() matches case-insensitively."""
    session = MockSession(replies=["Positive"])
    result = classify(session, "great", ["positive", "negative"])
    assert result == "positive"


def test_decide():
    """decide() returns chosen option."""
    session = MockSession(replies=["Option B"])
    result = decide(session, "Which one?", ["Option A", "Option B", "Option C"])
    assert result == "Option B"
