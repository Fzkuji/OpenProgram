"""The session and turn facts the permission package reads.

``openprogram.runtime.permissions`` sits below conversations and never
imports them; it asks :func:`openprogram.runtime.permissions.session_facts.facts`
instead. On import, this package registers the implementation below, which
answers from run control, plan mode, session settings, surface context,
the Agent registry and the session's chat model. Each answer looks its
source up when asked, so a test that patches the source sees its patch.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from openprogram.runtime.permissions.session_facts import SessionFacts, register


class ConversationSessionFacts(SessionFacts):
    def current_session_id(self) -> str | None:
        from openprogram.runtime.execution.run_control import get_current_session_id
        return get_current_session_id()

    def current_execution_id(self) -> str | None:
        from openprogram.runtime.execution.run_control import get_current_execution_id
        return get_current_execution_id()

    @contextmanager
    def bind_execution(self, execution_id: str | None) -> Iterator[None]:
        from openprogram.runtime.execution.run_control import (
            reset_current_execution_id,
            set_current_execution_id,
        )
        token = set_current_execution_id(execution_id)
        try:
            yield
        finally:
            reset_current_execution_id(token)

    def preapproved_wait_id(self) -> str | None:
        from openprogram.runtime.conversation.run_control import get_preapproved_wait_id
        return get_preapproved_wait_id()

    def turn_running(self, session_id: str, execution_id: str | None) -> bool:
        from openprogram.runtime.execution.run_control import current_token
        return current_token(session_id, execution_id=execution_id) is not None

    def project_defaults(self, session_id: str) -> dict:
        from openprogram.runtime.conversation import session_config
        return session_config.project_defaults(session_id)

    @staticmethod
    def _turn_branch(session_id: str) -> dict:
        """The branch of the turn running here, when it is in this conversation:
        a grant it records belongs to that branch, not to the selected one."""
        from openprogram.runtime.agent.turn_request import get_turn_request
        req = get_turn_request()
        branch = getattr(req, "branch_id", None) if getattr(req, "session_id", None) == session_id else None
        return {"branch_id": branch} if branch else {}

    def session_rules(self, session_id: str):
        from openprogram.runtime.conversation import session_config
        branch = self._turn_branch(session_id)
        if branch:
            from openprogram.runtime.context.session_db import default_db
            from openprogram.state.store.session.branch_config import config_for_branch
            row = default_db().get_session(session_id) or {}
            return session_config._config_from_row(config_for_branch(row, branch["branch_id"])).permission_rules
        return session_config.load_session_run_config(session_id).permission_rules

    def save_session_rules(self, session_id: str, agent_id: Any, rules: dict) -> None:
        from openprogram.runtime.conversation import session_config
        session_config.save_session_run_config(
            session_id, permission_rules=rules, **self._turn_branch(session_id),
        )

    def turn_request_for(self, store, execution) -> Any:
        from openprogram.runtime.conversation.production_driver import AgentActivationService
        resolver = AgentActivationService(
            lambda record: store.get_agent_turn_input(record.execution_id)
        )
        return resolver.build_request(execution, None)

    def browser_pages_available(self, surface_context: dict | None) -> bool:
        from openprogram.runtime.conversation import surface_context as surfaces
        return surfaces.browser_pages_available(surface_context)

    def holds_outbound(self, agent_id: str) -> bool:
        from openprogram.runtime.agent_registry import manager
        spec = manager.get(agent_id)
        return spec is not None and getattr(spec, "hold_outbound", False) is True

    def session_chat_model(self, session_id: str | None) -> tuple[Any, Any]:
        from openprogram.runtime.conversation import session_model
        return session_model.read_session_chat_model(session_id)

    def resolve_model(self, spec: str) -> Any:
        from openprogram.runtime.conversation import model_tools
        return model_tools.resolve_model({}, spec)


register(ConversationSessionFacts())
