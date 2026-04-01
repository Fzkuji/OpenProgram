"""
Tests for chaining functions together (sequential execution).
"""

from pydantic import BaseModel

from harness import function
from harness.session import Session
from harness.scope import Scope


class MockSession(Session):
    def __init__(self):
        self._history = []

    def send(self, message) -> str:
        self._history.append(message if isinstance(message, str) else str(message))
        return '{"status": "ok"}'

    @property
    def history_length(self) -> int:
        return len(self._history)


class StepResult(BaseModel):
    status: str


def test_sequential_functions_share_session():
    """Multiple functions can share one session (chained execution)."""
    session = MockSession()

    @function(return_type=StepResult)
    def step1(session: Session) -> StepResult:
        """Step 1."""

    @function(return_type=StepResult)
    def step2(session: Session) -> StepResult:
        """Step 2."""

    r1 = step1(session)
    r2 = step2(session)

    assert r1.status == "ok"
    assert r2.status == "ok"
    # Both used same session
    assert session.history_length == 2


def test_sequential_functions_separate_sessions():
    """Each function gets its own session (isolated execution)."""
    s1 = MockSession()
    s2 = MockSession()

    @function(return_type=StepResult)
    def step1(session: Session) -> StepResult:
        """Step 1."""

    @function(return_type=StepResult)
    def step2(session: Session) -> StepResult:
        """Step 2."""

    r1 = step1(s1)
    r2 = step2(s2)

    assert s1.history_length == 1
    assert s2.history_length == 1


def test_pipeline_pattern():
    """Functions can be chained in a pipeline, passing results forward."""
    session = MockSession()

    @function(return_type=StepResult)
    def observe(session: Session, task: str) -> StepResult:
        """Observe."""

    @function(return_type=StepResult)
    def act(session: Session, observation: str) -> StepResult:
        """Act based on observation."""

    r1 = observe(session, task="find button")
    r2 = act(session, observation=r1.status)

    assert r2.status == "ok"
