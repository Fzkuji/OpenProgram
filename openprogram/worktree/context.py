"""``_current_worktree_path`` ContextVar — the bridge between
worktree management and the in-process tools.

Mirrors the model used by
``openprogram.webui._pause_stop._current_session_id``: workers /
tool dispatchers bind the var at the top of an execution and reset
it on exit. Tools read it in their bodies (bash backend.run cwd,
edit/write/read relative-path resolution) without an explicit
parameter — the dispatcher owns the binding so the LLM-callable
surface doesn't grow a worktree argument.

The var defaults to ``None`` (no worktree). Tool fallback is
"behave as before": bash gets no cwd override, file tools require
absolute paths.

ContextVars do NOT propagate across ``threading.Thread`` start; the
TaskRunner already copies the parent context with ``contextvars.copy_context``
when submitting work to its pool, so a worktree bound in the dispatcher's
thread reaches workers transparently.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional


_current_worktree_path: ContextVar[Optional[str]] = ContextVar(
    "_current_worktree_path", default=None
)


def set_worktree(path: Optional[str]):
    """Bind the current worktree path. Returns the token for later
    :func:`reset_worktree` — callers should use a try/finally so they
    don't leak a binding across requests.

    Passing ``None`` explicitly is allowed and behaves the same as a
    no-op binding (still returns a token so caller code symmetry holds).
    """
    return _current_worktree_path.set(path)


def reset_worktree(token) -> None:
    """Reset the worktree binding using a token returned by
    :func:`set_worktree`. Silently swallow errors so cleanup paths
    can call this defensively."""
    try:
        _current_worktree_path.reset(token)
    except Exception:
        pass


def clear_worktree() -> None:
    """Force the worktree binding back to None for the current context
    without a token. Useful when a long-running runner picks up a new
    task that doesn't have a worktree."""
    _current_worktree_path.set(None)


def current_worktree_path() -> Optional[str]:
    """Read the current worktree path. ``None`` means "no active
    worktree, use default behaviour"."""
    return _current_worktree_path.get(None)


__all__ = [
    "set_worktree",
    "reset_worktree",
    "clear_worktree",
    "current_worktree_path",
]
