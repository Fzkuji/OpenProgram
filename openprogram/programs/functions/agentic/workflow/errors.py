"""Public exceptions for workflow authoring and execution."""

from __future__ import annotations


class InvalidWorkflow(ValueError):
    """Planner output is not an allowed workflow module."""


class WorkflowExecutionCapped(RuntimeError):
    """The workflow reached its real-call limit."""
