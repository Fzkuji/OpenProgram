"""Shared session/worktree binding helpers for the worktree tool family.

Each tool wraps :class:`openprogram.worktree.manager.WorktreeManager`
and returns a human-readable string so the LLM can act on the result
without parsing structured JSON. Errors surface as
``[worktree_<op> error] <code>: <detail>``.
"""
from __future__ import annotations

from typing import Optional


def _current_session_id() -> Optional[str]:
    """Pull the session id from the dispatcher's ContextVar — same
    source the agent tool uses."""
    try:
        from openprogram.agent.run_control import _current_session_id as _sid
        return _sid.get(None)
    except Exception:
        return None


def clear_binding_if_no_active(mgr, sid: Optional[str]) -> None:
    """After merge / discard / keep: if the session no longer has an
    active worktree, clear the ContextVar so subsequent tool calls go
    back to the source repo / default cwd."""
    if not sid:
        return
    if mgr.find_active_for_session(sid) is None:
        from openprogram.worktree.context import clear_worktree
        clear_worktree()
