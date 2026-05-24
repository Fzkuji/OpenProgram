"""session_db — git-backed SessionStore facade.

``SessionDB`` is now an alias for :class:`SessionStore` (see
``openprogram.store.session_store``). The old SQLite-backed
``DagSessionDB`` is retired; all session memory lives in
``~/.agentic/sessions-git/<session_id>/`` git repos.

The public method surface used by ``dispatcher``, channels, and the
WebUI is preserved by ``SessionStore`` — same 22 methods, same
semantics. See ``docs/design/git-as-entity-memory.md``.

Overrides
---------
``default_db()`` consults a ContextVar before falling back to the
process-wide singleton, so a sub-agent's dispatcher can route writes
to a worktree-rooted SessionStore without touching the parent's repo.
Use ``set_db_override`` / ``reset_db_override`` around a single turn.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

from openprogram.store import SessionStore, default_store


SessionDB = SessionStore


_db_override: ContextVar[Optional[SessionStore]] = ContextVar(
    "_session_db_override", default=None,
)


def default_db() -> SessionStore:
    """Process-wide singleton OR a ContextVar-scoped override.

    The override path is how sub-agent turns get a worktree-rooted
    store without monkey-patching the global. Plain chat turns leave
    the override unset and pick up the singleton."""
    ov = _db_override.get()
    if ov is not None:
        return ov
    return default_store()


def set_db_override(store: SessionStore) -> Token:
    """Bind ``store`` as ``default_db()``'s return for the current
    context. Returns the Token caller must pass to ``reset_db_override``
    to restore the previous binding. Mirrors ContextVar.set/reset.
    """
    return _db_override.set(store)


def reset_db_override(token: Token) -> None:
    """Restore the prior ``default_db()`` binding."""
    _db_override.reset(token)


__all__ = [
    "SessionDB",
    "default_db",
    "set_db_override",
    "reset_db_override",
]
