"""send_message tool family — branch-to-branch communication.

One primitive: ``send_message`` (deliver → trigger → auto-return),
plus its discovery tools ``list_sessions`` / ``list_branches``. One
tool per subdirectory; session/branch enumeration and the branch UI
emitter shared in ``shared.py``. Self-register via @function on
import. See docs/design/runtime/agent-collaboration.md.
"""
from .send_message import send_message
from .list_sessions import list_sessions
from .list_branches import list_branches

__all__ = ["send_message", "list_sessions", "list_branches"]
