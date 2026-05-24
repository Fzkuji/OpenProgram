"""session_db — git-backed SessionStore facade.

``SessionDB`` is now an alias for :class:`SessionStore` (see
``openprogram.store.session_store``). The old SQLite-backed
``DagSessionDB`` is retired; all session memory lives in
``~/.agentic/sessions-git/<session_id>/`` git repos.

The public method surface used by ``dispatcher``, channels, and the
WebUI is preserved by ``SessionStore`` — same 22 methods, same
semantics. See ``docs/design/git-as-entity-memory.md``.
"""
from __future__ import annotations

from openprogram.store import SessionStore, default_store


SessionDB = SessionStore


def default_db() -> SessionStore:
    """Process-wide singleton. Channels worker + webui server share
    this instance."""
    return default_store()


__all__ = ["SessionDB", "default_db"]
