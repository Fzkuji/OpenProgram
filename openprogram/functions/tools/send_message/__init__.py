"""send_message — branch-to-branch communication tools.

One primitive: ``send_message`` (deliver → trigger → auto-return).
Self-registers via @function on import. See
docs/design/runtime/agent-collaboration.md.
"""
from .send_message import send_message
from .list_branches import list_sessions, list_branches

__all__ = ["send_message", "list_sessions", "list_branches"]
