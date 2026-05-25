"""Git-backed session storage. Replaces the old SQLite ``DagSessionDB`` +
``GraphStore`` layer. Everything that used to live in
``openprogram/context/session_db.py`` and ``openprogram/context/storage.py``
moves here.

Public surface (re-exported for legacy import paths):

    SessionStore        ─ main class, implements DagSessionDB-compatible
                          22 public methods on top of git repos
    default_store()     ─ process-wide singleton
    GitSession          ─ per-session git repo wrapper (init / add /
                          commit / log / checkout)
    SessionMemoryIndex  ─ per-session in-memory DAG index, rebuilt
                          from git on startup / cache miss

Storage layout: every session is its own git repo at
``~/.agentic/sessions-git/<session_id>/`` with two top-level dirs:

    history/   append-only JSON files, one per DAG node, named
               ``NNNN-{u|a|t|s|...}-<id>.json`` where NNNN is the
               4-digit zero-padded seq. Never modified after write.

    context/   mutable LLM view. ``messages.json`` carries the current
               assembled message list (compact / aging rewrites it);
               ``commits/<id>.json`` carries per-commit ContextItem
               lists, written one file per turn (immutable).

Plus ``meta.json`` at the repo root for session-level fields (title,
agent_id, head_id, ...).

Git is the source of truth. The in-memory index is a query cache,
fully rebuildable from git on demand. See
``docs/design/git-as-entity-memory.md`` for the rationale.
"""
from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING

from .git_session import GitSession
from .memory_index import SessionMemoryIndex
from .graphstore_shim import GraphStoreShim

if TYPE_CHECKING:
    pass

# Dispatcher installs a per-turn (SessionStore, session_id) wrapper here
# so deep code (Runtime.exec, ask_user, @agentic_function decorator) can
# write DAG nodes via the same handle without threading args through
# every layer. Default None = standalone, no persistence (still works,
# just writes nothing).
_store: ContextVar[Optional[GraphStoreShim]] = ContextVar(
    "_store", default=None,
)

# Dispatcher installs the current turn's assistant_msg_id here so
# file-mutating tools can resolve which turn to attribute backups
# to (see openprogram/store/file_backup/helpers.py). Default None =
# no active turn; backup helper becomes a no-op.
_current_turn_id: ContextVar[Optional[str]] = ContextVar(
    "_current_turn_id", default=None,
)

# session_store is imported lazily by default_store() to keep import
# cost low for code paths that only need the lower-level classes.


__all__ = [
    "GitSession",
    "SessionMemoryIndex",
    "GraphStoreShim",
    "SessionStore",
    "default_store",
    "_store",
    "_current_turn_id",
]


def __getattr__(name):
    # Lazy: SessionStore + default_store are pulled in only when
    # actually referenced (most call sites use default_store()).
    if name == "SessionStore":
        from .session_store import SessionStore as _S
        return _S
    if name == "default_store":
        from .session_store import default_store as _ds
        return _ds
    raise AttributeError(name)
