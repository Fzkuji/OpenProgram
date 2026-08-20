"""Agent-facing worktree tool family.

Five ``@function`` LLM-callable tools wrap
:class:`openprogram.worktree.manager.WorktreeManager`, one per
subdirectory:

  * ``worktree_create`` — ``git worktree add`` on a source repo
  * ``worktree_merge``  — merge worktree branch back into source repo
  * ``worktree_discard``— ``git worktree remove --force`` + branch -D
  * ``worktree_list``   — list active / merged / discarded entries
  * ``worktree_keep``   — detach: keep the worktree dir + branch but
                          stop OpenProgram from binding to it

Session-binding helpers shared by the tools live in ``shared.py``.
Self-register via @function on import.
"""
from .worktree_create import worktree_create
from .worktree_merge import worktree_merge
from .worktree_discard import worktree_discard
from .worktree_keep import worktree_keep
from .worktree_list import worktree_list

__all__ = [
    "worktree_create",
    "worktree_merge",
    "worktree_discard",
    "worktree_list",
    "worktree_keep",
]
