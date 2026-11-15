"""session_db — SessionStore facade.

``SessionDB`` is now an alias for :class:`SessionStore` (see
``openprogram.state.store.session.session_store``). The old SQLite-backed
``DagSessionDB`` is retired; every conversation lives in its project's
folder, ``~/.openprogram/projects/<project-id>/chats/<session_id>/``.

The public method surface used by ``dispatcher``, channels, and the
WebUI is preserved by ``SessionStore`` — same 22 methods, same
semantics. See ``docs/design/memory/git-as-entity-memory.md``.
"""
from __future__ import annotations

from openprogram.state.store import SessionStore, default_store


SessionDB = SessionStore


def default_db() -> SessionStore:
    """Use the active Context store, or the process-wide conversation store."""
    from openprogram.runtime.context.model import Context
    context = Context.current()
    if context is not None and context.store is not None:
        return context.store.store
    return default_store()


__all__ = ["SessionDB", "default_db"]
