"""
Workflow — ordered execution of Steps.

A Workflow is a sequence of Steps where:
    - Steps execute in order
    - Each Step's output is added to the shared context
    - The next Step reads from that updated context
    - If any Step fails, the Workflow stops and reports which Step failed

Each Step can use a different Session — mix and match platforms freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from harness.step import Step, StepFailure
from harness.session import Session


@dataclass
class StepConfig:
    """
    Binds a Step to a Session for execution within a Workflow.

    Args:
        step:     The Step to execute
        session:  The Session to use as runtime for this step
                  (if None, uses the Workflow's default session)
    """
    step: Step
    session: Optional[Session] = None


@dataclass
class WorkflowResult:
    """The result of a completed Workflow execution."""
    success: bool
    context: dict
    failed_step: Optional[str] = None
    error: Optional[str] = None


class Workflow:
    """
    Executes an ordered sequence of Steps.

    Example:
        workflow = Workflow(
            steps=[
                StepConfig(step=observe_step, session=anthropic_session),
                StepConfig(step=learn_step,   session=openclaw_session),
                StepConfig(step=act_step,      session=anthropic_session),
                StepConfig(step=verify_step,   session=anthropic_session),
            ],
            default_session=anthropic_session,
        )
        result = workflow.run(task="Click the login button")
    """

    def __init__(
        self,
        steps: list[StepConfig],
        default_session: Optional[Session] = None,
    ):
        """
        Args:
            steps:            Ordered list of StepConfig (step + optional session)
            default_session:  Fallback session for steps without an explicit session
        """
        self.steps = steps
        self.default_session = default_session

    def run(self, task: str, initial_context: Optional[dict] = None) -> WorkflowResult:
        """
        Execute the workflow.

        Args:
            task:             The task description passed to every Step
            initial_context:  Optional starting context (merged with task)

        Returns:
            WorkflowResult with success status and final context
        """
        context = dict(initial_context or {})
        context["task"] = task

        for config in self.steps:
            step = config.step
            session = config.session or self.default_session

            if session is None:
                raise ValueError(
                    f"Step '{step.name}' has no session. "
                    f"Either set a session in StepConfig or provide a default_session."
                )

            try:
                result = step.run(session=session, context=context)
                # Write step output back into context
                context[step.name] = result.model_dump()
            except StepFailure as e:
                return WorkflowResult(
                    success=False,
                    context=context,
                    failed_step=e.step_name,
                    error=str(e),
                )

        return WorkflowResult(success=True, context=context)
