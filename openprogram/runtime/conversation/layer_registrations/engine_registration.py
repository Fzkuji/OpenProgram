"""The conversation engine's implementation of the call core's engine link.

``openprogram.runtime.agent.engine_link`` is how the call core reaches the
engine above it without importing it. This module implements that link with
the conversation packages (``turn_runtime``, ``run_control``, ``checkpoint``,
``model_tools``) and the background job runner, and
registers it when ``openprogram.runtime.conversation`` is imported.

Every operation imports its implementation when it runs, so the engine's
modules load on first use (as they did when the core imported them lazily)
and a test that patches one of them is seen.
"""
from __future__ import annotations

from typing import Any

from openprogram.runtime.agent import engine_link


class ConversationEngine:
    """Persistent turns, worker shutdown, job activity and resumable loops."""

    def run_turn(self, request, *, on_event, cancel_event, execution_context):
        from openprogram.runtime.conversation.turn_runtime import execute_turn
        return execute_turn(request, on_event=on_event, cancel_event=cancel_event,
                            execution_context=execution_context)

    def resume_turn(self, continuation, *, on_event, cancel_event, execution_context):
        from openprogram.runtime.conversation.turn_runtime import execute_continuation
        return execute_continuation(continuation, on_event=on_event,
                                    cancel_event=cancel_event,
                                    execution_context=execution_context)

    def agent_profile(self, agent_id: str) -> dict:
        from openprogram.runtime.conversation.turn_runtime import _load_agent_profile
        return _load_agent_profile(agent_id)

    def default_agent_profile(self) -> dict:
        from openprogram.runtime.agent_registry import manager
        from openprogram.runtime.conversation.model_tools import load_agent_profile
        return load_agent_profile(getattr(manager, "DEFAULT_AGENT_ID", "main"))

    def resolve_tools(self, profile: dict, override: Any = None):
        from openprogram.runtime.conversation.model_tools import resolve_tools
        return resolve_tools(profile, override=override)

    def preapproved_wait(self, wait_id: str):
        return _PreapprovedWait(wait_id)

    def worker_stopping(self) -> bool:
        from openprogram.runtime.conversation.run_control import is_worker_stopping
        return is_worker_stopping()

    def record_job_activity(self, kind: str) -> None:
        from openprogram.runtime.jobs.runner import record_current_job_activity
        record_current_job_activity(kind)

    def job_operation_timeout(self, declared):
        from openprogram.runtime.jobs.runner import current_job_operation_timeout
        return current_job_operation_timeout(declared)

    def runtime_contract_snapshot(self, **fields: Any) -> dict:
        from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot
        return runtime_contract_snapshot(**fields)


class _PreapprovedWait:
    """Bind a pre-approved wait id around one tool call."""

    def __init__(self, wait_id: str) -> None:
        self._wait_id = wait_id
        self._token = None

    def __enter__(self) -> None:
        from openprogram.runtime.conversation.run_control import set_preapproved_wait_id
        self._token = set_preapproved_wait_id(self._wait_id)

    def __exit__(self, *_exc) -> None:
        from openprogram.runtime.conversation.run_control import reset_preapproved_wait_id
        reset_preapproved_wait_id(self._token)


ENGINE = ConversationEngine()

engine_link.register(ENGINE)
