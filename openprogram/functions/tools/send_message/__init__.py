"""send_message tool family — branch-to-branch communication.

One primitive: ``send_message`` (deliver → trigger → auto-return),
plus its discovery tool ``list_agents`` and the lifecycle tool
``archive_agent``. One tool per subdirectory; session/branch
enumeration and the branch UI emitter shared in ``shared.py``.
Self-register via @function on import. See
docs/reference/design/runtime/agent-collaboration.md.
"""
from .send_message import send_message
from .list_agents import list_agents
from .archive_agent import archive_agent

__all__ = ["send_message", "list_agents", "archive_agent"]
