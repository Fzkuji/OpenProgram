"""
Tests for the Workflow class.
"""

import pytest
from pydantic import BaseModel
from harness.step import Step, StepFailure
from harness.session import Session
from harness.workflow import Workflow, StepConfig


class MockSession(Session):
    def __init__(self, replies: list[str]):
        self._replies = iter(replies)

    def send(self, message: str) -> str:
        return next(self._replies)


class Step1Result(BaseModel):
    data: str


class Step2Result(BaseModel):
    processed: str


def test_workflow_runs_steps_in_order():
    """Workflow executes all steps and accumulates context."""
    session = MockSession([
        '{"data": "from step1"}',
        '{"processed": "from step2"}',
    ])

    step1 = Step("step1", "First step", "Do step 1", Step1Result)
    step2 = Step("step2", "Second step", "Do step 2", Step2Result)

    workflow = Workflow(
        steps=[StepConfig(step1), StepConfig(step2)],
        default_session=session,
    )
    result = workflow.run(task="test task")

    assert result.success is True
    assert result.context["step1"]["data"] == "from step1"
    assert result.context["step2"]["processed"] == "from step2"


def test_workflow_stops_on_step_failure():
    """Workflow stops and reports failure when a step fails."""
    session = MockSession([
        "not valid json",  # step1 fails all retries
        "not valid json",
        "not valid json",
    ])

    step1 = Step("step1", "First step", "Do step 1", Step1Result, max_retries=3)
    step2 = Step("step2", "Second step", "Do step 2", Step2Result)

    workflow = Workflow(
        steps=[StepConfig(step1), StepConfig(step2)],
        default_session=session,
    )
    result = workflow.run(task="test task")

    assert result.success is False
    assert result.failed_step == "step1"
    assert "step2" not in result.context


def test_workflow_passes_context_between_steps():
    """Each step can read output of previous steps via context."""
    received_messages = []

    class CapturingSession(Session):
        replies = iter([
            '{"data": "step1 output"}',
            '{"processed": "used step1 data"}',
        ])

        def send(self, message: str) -> str:
            received_messages.append(message)
            return next(self.replies)

    step1 = Step("step1", "First", "Do 1", Step1Result)
    step2 = Step("step2", "Second", "Do 2", Step2Result, reads=["task", "step1"])

    workflow = Workflow(
        steps=[StepConfig(step1), StepConfig(step2)],
        default_session=CapturingSession(),
    )
    workflow.run(task="my task")

    # step2's message should contain step1's output
    assert "step1 output" in received_messages[1]
