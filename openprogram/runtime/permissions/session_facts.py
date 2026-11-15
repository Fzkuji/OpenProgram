"""What the permission package needs to know about the running conversation.

Permission decisions read a few facts that the conversation package owns:
which session and execution the current task is bound to, whether a turn
is running, the session's plan mode and stored rules, its project's
defaults, and the model it chats on. The permission package sits below
the conversation package and never imports it; instead the conversation
package registers a :class:`SessionFacts` implementation on import
(``openprogram.runtime.conversation.permission_registration``).

The defaults describe a process in which no conversation exists: nothing
is bound, no turn runs, no session holds rules. Each of those values can
only become something else through the conversation package, and
importing any part of it registers the real facts first.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


class SessionFacts:
    """Session and turn facts, answered as if no conversation exists."""

    def current_session_id(self) -> str | None:
        """The session bound to the current task."""
        return None

    def current_execution_id(self) -> str | None:
        """The canonical execution bound to the current task."""
        return None

    @contextmanager
    def bind_execution(self, execution_id: str | None) -> Iterator[None]:
        """Bind ``execution_id`` to the current task inside the block."""
        yield

    def preapproved_wait_id(self) -> str | None:
        """The approval wait a resumed tool call was admitted with."""
        return None

    def turn_running(self, session_id: str, execution_id: str | None) -> bool:
        """Whether a turn of ``execution_id`` is running on the session."""
        return False

    def project_defaults(self, session_id: str) -> dict:
        """The session's project defaults (``permission_mode`` and others)."""
        return {}

    def session_rules(self, session_id: str):
        """The session layer's ``PermissionRules``, or None."""
        return None

    def save_session_rules(self, session_id: str, agent_id: Any, rules: dict) -> None:
        """Store the session layer's rules (``{"allow", "deny", "ask"}``)."""
        raise RuntimeError("session settings are unavailable")

    def turn_request_for(self, store, execution) -> Any:
        """The turn request an execution was admitted with."""
        raise RuntimeError("conversation turns are unavailable")

    def browser_pages_available(self, surface_context: dict | None) -> bool:
        """Whether the turn's surface grants the built-in browser Pages."""
        return False

    def holds_outbound(self, agent_id: str) -> bool:
        """Whether the Agent holds what it sends on the user's behalf as a draft."""
        return False

    def session_chat_model(self, session_id: str | None) -> tuple[Any, Any]:
        """``(provider, model_id)`` the session chats on."""
        return None, None

    def resolve_model(self, spec: str) -> Any:
        """The model object for ``provider/model_id``, as a turn resolves it."""
        raise LookupError(spec)


_facts: SessionFacts = SessionFacts()


def register(facts: SessionFacts | None) -> None:
    """Install the facts implementation (``None`` restores the defaults)."""
    global _facts
    _facts = facts if facts is not None else SessionFacts()


def facts() -> SessionFacts:
    """The registered facts implementation."""
    return _facts


__all__ = ["SessionFacts", "facts", "register"]
