"""agent_collab — branch-to-branch communication tools.

One primitive: ``message_branch`` (deliver → trigger → auto-return).
Self-registers via @function on import. See
docs/design/runtime/agent-collaboration.md.
"""
from .message_branch import message_branch

__all__ = ["message_branch"]
