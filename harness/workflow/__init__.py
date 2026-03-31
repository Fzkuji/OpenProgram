"""
Workflow — ordered execution of Functions.

A Workflow is a sequence of Functions where:
    - Functions execute in order
    - Each Function's return value is added to the shared context
    - The next Function reads from that updated context
    - If any Function raises FunctionError, the Workflow stops

Each Function can use a different Session — mix and match platforms freely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from harness.function import Function, FunctionError
from harness.session import Session


@dataclass
class FunctionCall:
    """
    Binds a Function to a Session for execution within a Workflow.

    Analogous to a function call in a program — it specifies which
    function to call and which runtime (Session) to use.

    Args:
        function:  The Function to call
        session:   The Session to use as runtime
                   (if None, uses the Workflow's default session)
    """
    function: Function
    session: Optional[Session] = None


@dataclass
class WorkflowResult:
    """
    The result of a completed Workflow execution.

    Attributes:
        success:         True if all Functions returned successfully
        context:         The final accumulated context
        failed_function: Name of the Function that failed (if any)
        error:           Error message (if any)
    """
    success: bool
    context: dict
    failed_function: Optional[str] = None
    error: Optional[str] = None


class Workflow:
    """
    Executes an ordered sequence of Functions.

    Each Function's return value is stored in context under its name,
    making it available to all subsequent Functions.

    Example:
        workflow = Workflow(
            calls=[
                FunctionCall(function=observe, session=anthropic_session),
                FunctionCall(function=learn,   session=openclaw_session),
                FunctionCall(function=act,     session=anthropic_session),
                FunctionCall(function=verify,  session=anthropic_session),
            ],
            default_session=anthropic_session,
        )
        result = workflow.run(task="Click the login button")
        # result.context["observe"] → ObserveResult dict
        # result.context["learn"]   → LearnResult dict
    """

    def __init__(
        self,
        calls: list[FunctionCall],
        default_session: Optional[Session] = None,
    ):
        """
        Args:
            calls:            Ordered list of FunctionCalls
            default_session:  Fallback session for calls without an explicit session
        """
        self.calls = calls
        self.default_session = default_session

    def run(self, task: str, initial_context: Optional[dict] = None) -> WorkflowResult:
        """
        Run the workflow.

        Args:
            task:             The task description, added to context as "task"
            initial_context:  Optional starting context

        Returns:
            WorkflowResult with success status and final context
        """
        context = dict(initial_context or {})
        context["task"] = task

        for call in self.calls:
            function = call.function
            session = call.session or self.default_session

            if session is None:
                raise ValueError(
                    f"Function '{function.name}' has no session. "
                    f"Provide a session in FunctionCall or set a default_session."
                )

            try:
                return_value = function.call(session=session, context=context)
                # Store return value in context under the function's name
                context[function.name] = return_value.model_dump()
            except FunctionError as e:
                return WorkflowResult(
                    success=False,
                    context=context,
                    failed_function=e.function_name,
                    error=str(e),
                )

        return WorkflowResult(success=True, context=context)
