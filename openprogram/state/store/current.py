"""The session store for the code that is running right now.

A Context the runtime has entered may carry its own store (a test, an
embedded host, a probe with its own directory). Which store that is only
the runtime knows, and the store sits below the runtime, so the runtime
registers a resolver here when it loads. Store code that needs "the
current session's store" calls :func:`current_store`; with no resolver
(the runtime is not loaded, so no Context can be active) it is the
process-wide store.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.state.store.session.session_store import SessionStore

_resolver: Callable[[], "SessionStore"] | None = None


def register_current_store_resolver(resolver: Callable[[], "SessionStore"]) -> None:
    """Make ``resolver()`` the way this process finds the current store."""
    global _resolver
    _resolver = resolver


def current_store() -> "SessionStore":
    """The active Context's store, or the process-wide conversation store."""
    if _resolver is not None:
        return _resolver()
    from openprogram.state.store.session.session_store import default_store

    return default_store()


__all__ = ["current_store", "register_current_store_resolver"]
