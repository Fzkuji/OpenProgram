"""Storage: session memory, the project entity layer, and the
file revert/record machinery. See ``README.md`` in this directory for the
full map; this docstring covers the original session-storage core.

The directory holds three groups (README has the details):

  1. **Session storage** — one append-only log per conversation:
     ``session_store`` / ``session_log`` / ``memory_index`` /
     ``session_node_writer`` / ``_msg_adapter`` / ``search``.
  2. **Project entity layer + auto-commit** — ``project_store`` (the
     user's working dir as a git-backed Project, with safe auto-init and
     a reset/revert-of-a-commit primitive) and ``project_commit`` (wires
     the agent's per-turn edits into that repo). See
     ``docs/design/memory/overview.md`` and ``docs/design/runtime/file-management.md``.
  3. **Revert / record helpers** — ``checkpoint/`` (per-turn file
     checkpoints = the "undo" layer) and ``read_tracking`` (read-before-
     edit concurrency guard). See ``docs/design/runtime/file-management.md``.

Public surface (re-exported for legacy import paths):

    SessionStore        ─ main class, the query and write API over logs
    default_store()     ─ process-wide singleton
    SessionLog          ─ one session's append-only records
    SessionMemoryIndex  ─ per-session in-memory DAG index, rebuilt
                          from the log on startup / cache miss

Storage layout: every session is a directory in its project's folder,
``~/.openprogram/projects/<project>/chats/<session_id>/`` (chats with no
project belong to the home project, ``default``). Older chats recorded
inside a project folder are found through
``projects/.chats-locations.json``. A session holds:

    main.jsonl  the append-only log, written by the worker
    agents/     one more log per other writing process
    context/    per-commit ContextItem lists, one immutable file per turn

The log is the source of truth. The in-memory index is a query cache,
rebuilt from the log on demand.
"""
from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING

# Files live in the session/ project/ snapshot/ sub-packages (group ①②③,
# see README). The top-level package re-exports the historical public
# surface so ``from openprogram.state.store import SessionStore`` etc. keep
# working unchanged after the physical regroup.
from .session import SessionLog, SessionMemoryIndex, SessionNodeWriter
# Provenance read-layer — the LLM-free seam memory maps from
# (docs/design/memory/entity-session-cache.md §5).
from .session import (
    Provenance,
    iter_nodes_since,
    node_provenance,
    project_commits,
    session_project_id,
)

if TYPE_CHECKING:
    pass

# Dispatcher installs a per-turn (SessionStore, session_id) wrapper here
# so deep code (Runtime.exec, ask_user, Agent method decorator) can
# write DAG nodes via the same handle without threading args through
# every layer. Default None = standalone, no persistence (still works,
# just writes nothing).
_store: ContextVar[Optional[SessionNodeWriter]] = ContextVar(
    "_store", default=None,
)

# Dispatcher installs the current turn's assistant_msg_id here so
# file-mutating tools can resolve which turn to attribute checkpoints
# to (see store/snapshot/checkpoint/helpers.py). Default None =
# no active turn; backup helper becomes a no-op.
_current_turn_id: ContextVar[Optional[str]] = ContextVar(
    "_current_turn_id", default=None,
)

# session_store is imported lazily by default_store() to keep import
# cost low for code paths that only need the lower-level classes.


from contextlib import contextmanager


@contextmanager
def session_scope(store, session_id: str):
    """Route DAG writes to ``store``/``session_id`` for the duration.

    The public face of ``_store`` for embedders: inside the block,
    ``Runtime.exec`` and ``Agent method`` persist their nodes to
    the given session; on exit the previous binding is restored.
    """
    token = _store.set(SessionNodeWriter(store, session_id))
    try:
        yield
    finally:
        _store.reset(token)


__all__ = [
    "SessionLog",
    "SessionMemoryIndex",
    "SessionNodeWriter",
    "SessionStore",
    "default_store",
    "session_scope",
    "_store",
    "_current_turn_id",
    "Provenance",
    "iter_nodes_since",
    "node_provenance",
    "project_commits",
    "session_project_id",
]


def __getattr__(name):
    # Lazy: SessionStore + default_store are pulled in only when
    # actually referenced (most call sites use default_store()).
    if name == "SessionStore":
        from .session.session_store import SessionStore as _S
        return _S
    if name == "default_store":
        from .session.session_store import default_store as _ds
        return _ds
    raise AttributeError(name)
