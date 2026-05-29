"""Agent Worktree subsystem.

Provides the ``git worktree`` lifecycle (create / merge / discard / list /
keep) used by agent tools to make isolated changes to the user's real
repository before deciding whether to land them.

Public surface:

  * :class:`Worktree`, :class:`WorktreeStatus` — entity + state machine
    (``openprogram.worktree.types``).
  * :func:`get_manager` — process-wide :class:`WorktreeManager` singleton
    (``openprogram.worktree.manager``).
  * :func:`set_worktree` / :func:`current_worktree_path` — ContextVar
    helpers (``openprogram.worktree.context``).

The worktree directory is **never** placed inside ``~/.openprogram/sessions/``
— that subtree belongs to OpenProgram's own conversation-memory git
repositories. See ``docs/design/agent-worktree.md`` Part 1 D4.
"""
from .context import (
    current_worktree_path,
    set_worktree,
    clear_worktree,
)
from .manager import WorktreeManager, get_manager
from .types import Worktree, WorktreeStatus

__all__ = [
    "Worktree",
    "WorktreeStatus",
    "WorktreeManager",
    "get_manager",
    "current_worktree_path",
    "set_worktree",
    "clear_worktree",
]
